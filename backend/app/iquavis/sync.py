from __future__ import annotations

from collections import Counter
import json
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
import hashlib
from pathlib import Path
import re
import sqlite3
from threading import RLock
from typing import Any

from app.iquavis.client import (
    IQuavisApiError,
    IQuavisClient,
    IQuavisConflictError,
    IQuavisRemoteTask,
    IQuavisRetryableError,
)
from app.iquavis.editor_conflict import (
    IQUAVIS_DERIVED_CONFLICT_MESSAGE,
    IquavisEditorConflictSyncResult,
    IquavisEditorConflictSyncService,
    QOLONY_TASK_STATUSES,
    SUPPORTED_SYNC_FIELDS,
    as_written_conflict_field,
    is_iquavis_derived_conflict,
    is_unmeasured_progress_rate,
)
from app.iquavis.editor_forced_import import (
    ForcedTaskDecision,
    classify_forced_import_task,
)
from app.iquavis.editor_import import IquavisEditorImportResult, IquavisEditorImportService
from app.iquavis.editor_models import (
    IquavisEditorQolonyUpdate,
    IquavisEditorSnapshot,
    IquavisTaskSyncProperties,
    IquavisTaskSyncRecord,
    SyncedIquavisTarget,
)
from app.iquavis.editor_workbook import (
    IquavisEditorWorkbookAdapter,
    IquavisEditorWorkbookError,
    OpenpyxlIquavisEditorWorkbookAdapter,
)
from app.iquavis.excel_server_save import (
    ExcelServerSaveError,
    format_excel_server_save_event,
    run_excel_server_save,
)
from app.iquavis.link_targets import clear_force_sync_requests, read_sync_targets
from app.iquavis.sync_state import (
    IQUAVIS_CONFLICT_STATE,
    IQUAVIS_CONFLICT_HOLD_STATE,
    IQUAVIS_PENDING_SAVE_STATE,
    IQUAVIS_TASK_CONFLICT_STATE_KEY,
    IquavisTaskConflict,
    IquavisTaskConflictField,
    parse_conflict_state,
    serialize_conflict_state,
)
from app.iquavis.xlsm_import import load_selected_tasks_from_xlsm
from app.tms_core.db.models import Task
from app.tms_core.db.repository import (
    IquavisTaskBaselineInput,
    NewTaskInput,
    TaskImportUpdate,
    TmsRepository,
)
from app.tms_core.domain.rounding import round_half_up
from app.tms_core.services.core import TmsService, UserInputError


LATEST_SYNC_EVENT_STATE_KEY = "IQuavisLatestSyncEvent"
LAST_PULL_AT_STATE_KEY = "IQuavisLastPullAt"
LAST_EDITOR_DAILY_SYNC_DATE_STATE_KEY = "IQuavisEditorLastDailySyncDate"
IQUAVIS_AUTHORITATIVE_IMPORT_CONFIRMATION = "IQUAVIS_TO_QOLONY"
IQUAVIS_FORCED_IMPORT_CONFIRMATION = "IQUAVIS_FORCE_TO_QOLONY"
IQUAVIS_FORCED_CONFLICT_MESSAGE = (
    "QOLONY側とiQUAVIS側で、同じ項目がそれぞれ別の値に変更されています。"
    "どちらを正とするか選択してください。"
)
IQUAVIS_AUTHORITATIVE_COMPARE_FIELDS: tuple[str, ...] = (
    "name",
    "start_date",
    "end_date",
    "assigned_minutes",
    "actual_minutes",
    "status",
    "progress_rate",
    "memo",
)
IQUAVIS_AUTHORITATIVE_DETAIL_LIMIT = 50


def _excel_time_text_seconds(value: Any) -> int | None:
    """Excel時刻表示とシリアル値を同じ秒数へ正規化する。"""
    text = str(value).strip()
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", text)
    if match is not None:
        hour, minute, second = (int(part or 0) for part in match.groups())
        if hour < 24 and minute < 60 and second < 60:
            return hour * 3600 + minute * 60 + second
        return None
    try:
        serial = float(text)
    except (TypeError, ValueError):
        return None
    if 0.0 <= serial < 1.0:
        return round(serial * 24 * 60 * 60) % (24 * 60 * 60)
    return None


def _effective_iquavis_id(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    normalized = text.replace("\u3000", "").strip()
    if not normalized:
        return ""
    lowered = normalized.lower()
    if lowered in {
        "0",
        "0.0",
        "#n/a",
        "n/a",
        "#na",
        "na",
        "null",
        "#null",
        "none",
        "#none",
        "undefined",
        "#undefined",
        "nan",
        "#nan",
    }:
        return ""
    return normalized


def _authoritative_values_equal(field_name: str, previous: Any, visible: Any) -> bool:
    if previous == visible:
        return True
    if field_name in {"name", "memo"}:
        previous_text = str(previous).strip()
        visible_text = str(visible).strip()
        if previous_text == visible_text:
            return True
        previous_seconds = _excel_time_text_seconds(previous_text)
        visible_seconds = _excel_time_text_seconds(visible_text)
        return previous_seconds is not None and previous_seconds == visible_seconds
    if field_name == "status":
        return str(previous).strip() == str(visible).strip()
    return False


@dataclass(frozen=True)
class SyncEvent:
    message: str
    is_error: bool
    recorded_at: datetime


@dataclass(frozen=True)
class SpreadsheetImportResult:
    imported_local_ids: list[str]
    selected_count: int
    skipped_existing_count: int
    duplicate_count: int


@dataclass(frozen=True)
class _ForcedImportPlan:
    """強制取込の判定結果。DBを変更する前の状態をそのまま持つ。"""

    workbook_path: Path
    workbook_fingerprint: str
    snapshot: IquavisEditorSnapshot
    decisions: tuple[ForcedTaskDecision, ...]
    iquavis_props_by_task_id: dict[str, IquavisTaskSyncProperties]
    new_task_iquavis_ids: tuple[str, ...]
    local_only_task_count: int
    missing_qolony_baseline_count: int
    missing_workbook_baseline_count: int
    excluded_record_count: int
    duplicate_local_link_ids: tuple[str, ...]

    @property
    def conflict_decisions(self) -> tuple[ForcedTaskDecision, ...]:
        return tuple(decision for decision in self.decisions if decision.is_conflict)

    @property
    def adopt_decisions(self) -> tuple[ForcedTaskDecision, ...]:
        return tuple(
            decision for decision in self.decisions if not decision.is_conflict and decision.has_adoptions
        )

    def to_preview(self) -> dict[str, Any]:
        conflicts = self.conflict_decisions
        adoptions = self.adopt_decisions
        conflict_field_counter: Counter[str] = Counter()
        for decision in conflicts:
            for item in decision.conflict_fields:
                conflict_field_counter[item.field] += 1
        adopted_field_counter: Counter[str] = Counter()
        for decision in adoptions:
            for item in decision.adopt_fields:
                adopted_field_counter[item.field] += 1
        return {
            "workbook_name": self.workbook_path.name,
            "workbook_fingerprint": self.workbook_fingerprint,
            "scanned_task_count": len(self.decisions) + len(self.new_task_iquavis_ids),
            "new_task_count": len(self.new_task_iquavis_ids),
            "updated_task_count": len(adoptions),
            "unchanged_task_count": len(self.decisions) - len(adoptions) - len(conflicts),
            "conflict_task_count": len(conflicts),
            "local_only_task_count": self.local_only_task_count,
            "excluded_record_count": self.excluded_record_count,
            "missing_qolony_baseline_count": self.missing_qolony_baseline_count,
            "missing_workbook_baseline_count": self.missing_workbook_baseline_count,
            "duplicate_local_link_count": len(self.duplicate_local_link_ids),
            "adopted_field_counts": dict(sorted(adopted_field_counter.items())),
            "conflict_field_counts": dict(sorted(conflict_field_counter.items())),
            "conflict_details": [
                {
                    "iquavis_id": decision.iquavis_id,
                    "task_id": decision.task_id,
                    "fields": [
                        {
                            "field": item.field,
                            "baseline": item.baseline,
                            "qolony": item.qolony,
                            "iquavis": item.iquavis,
                        }
                        for item in decision.conflict_fields
                    ],
                }
                for decision in conflicts[:IQUAVIS_AUTHORITATIVE_DETAIL_LIMIT]
            ],
            "confirmation": IQUAVIS_FORCED_IMPORT_CONFIRMATION,
        }


@dataclass(frozen=True)
class EditorWorkbookSyncResult:
    imported: IquavisEditorImportResult
    conflicts: IquavisEditorConflictSyncResult


class IquavisConflictPermissionError(UserInputError):
    pass


class IquavisConflictStaleError(UserInputError):
    pass


def _sync_json_value(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    return value


class IQuavisSyncService:
    def __init__(
        self,
        *,
        repository: TmsRepository,
        tms_service: TmsService,
        client: IQuavisClient | None = None,
    ):
        self.repository = repository
        self.tms_service = tms_service
        self.settings = tms_service.settings
        self.client = client or IQuavisClient(self.settings.iquavis)
        self._editor_workbook_lock = RLock()

    def is_enabled(self) -> bool:
        config = self.settings.iquavis
        return bool(config.enabled and config.base_url and config.username and config.password)

    def close(self) -> None:
        self.client.close()

    def queue_task_schedule_sync(self, task_id: str) -> bool:
        if not self.is_enabled():
            return False
        task = self.repository.get_task(task_id)
        iquavis_task_id = _effective_iquavis_id(task.IQuavisTaskId) if task is not None else ""
        if task is None or not iquavis_task_id:
            return False
        if self.tms_service.is_iquavis_sync_excluded(task.Id):
            return False
        payload_json = json.dumps(
            {
                "task_name": task.Name,
                "start_date": task.StartDate.isoformat(),
                "end_date": task.EndDate.isoformat(),
            },
            ensure_ascii=False,
        )
        self.repository.enqueue_iquavis_sync_job(
            task_id=task.Id,
            iquavis_task_id=iquavis_task_id,
            job_type="Schedule",
            payload_json=payload_json,
            next_run_at=datetime.now(),
        )
        return True

    def queue_task_progress_sync(self, task_id: str) -> bool:
        if not self.is_enabled():
            return False
        task = self.repository.get_task(task_id)
        iquavis_task_id = _effective_iquavis_id(task.IQuavisTaskId) if task is not None else ""
        if task is None or not iquavis_task_id:
            return False
        if task.Type != "Task":
            return False
        if self.tms_service.is_iquavis_sync_excluded(task.Id):
            return False

        payload_json = json.dumps(
            {
                "task_name": task.Name,
                "status": task.Status,
                "progress_rate": self._progress_rate_as_int(task.ProgressRate),
            },
            ensure_ascii=False,
        )
        self.repository.enqueue_iquavis_sync_job(
            task_id=task.Id,
            iquavis_task_id=iquavis_task_id,
            job_type="Progress",
            payload_json=payload_json,
            next_run_at=datetime.now(),
        )
        return True

    def queue_progress_sync_for_task_ids(self, task_ids: set[str]) -> int:
        queued = 0
        for task_id in sorted(task_ids):
            if self.queue_task_progress_sync(task_id):
                queued += 1
        return queued

    def process_outbound_jobs(self, *, now: datetime | None = None, limit: int = 20) -> int:
        if not self.is_enabled():
            return 0

        now = now or datetime.now()
        jobs = self.repository.list_due_iquavis_sync_jobs(now=now, limit=limit)
        processed = 0

        for job in jobs:
            processed += 1
            self.repository.mark_iquavis_sync_job_running(job.Id, started_at=now)
            try:
                task = self.repository.get_task(job.TaskId)
                local_task_id = _effective_iquavis_id(task.IQuavisTaskId) if task is not None else ""
                if task is None or not local_task_id or local_task_id != _effective_iquavis_id(job.IQuavisTaskId):
                    self.repository.mark_iquavis_sync_job_failed(
                        job.Id,
                        finished_at=datetime.now(),
                        error="ペアリング解除済みのため同期対象から外しました",
                        http_status=None,
                    )
                    continue
                if self.tms_service.is_iquavis_sync_excluded(task.Id):
                    self.repository.mark_iquavis_sync_job_succeeded(
                        job.Id,
                        finished_at=datetime.now(),
                        http_status=None,
                    )
                    continue
                if not self._is_remote_project_allowed(task.IQuavisProjectId):
                    self.repository.mark_iquavis_sync_job_failed(
                        job.Id,
                        finished_at=datetime.now(),
                        error="許可対象外のiQUAVISプロジェクトです",
                        http_status=None,
                    )
                    self._publish_event("iQUAVIS同期をスキップしました: 許可対象外のプロジェクトです", True)
                    continue

                payload = json.loads(job.PayloadJson)
                self._execute_outbound_job(task=task, job_type=job.JobType, payload=payload)
                finished_at = datetime.now()
                self.repository.mark_task_iquavis_last_pushed(task.Id, finished_at)
                self.repository.mark_iquavis_sync_job_succeeded(
                    job.Id,
                    finished_at=finished_at,
                    http_status=200,
                )
                self._publish_event(
                    f"iQUAVIS同期: {task.Name} の{self._job_type_label(job.JobType)}を反映しました",
                    False,
                )
            except IQuavisRetryableError as exc:
                finished_at = datetime.now()
                next_run_at = finished_at + timedelta(seconds=self._retry_delay_seconds(job.Attempts + 1))
                self.repository.mark_iquavis_sync_job_retry(
                    job.Id,
                    next_run_at=next_run_at,
                    finished_at=finished_at,
                    error=str(exc),
                    http_status=exc.status_code,
                )
                self._publish_event(
                    f"iQUAVIS同期を再試行します: {str(exc)}",
                    True,
                )
            except IQuavisApiError as exc:
                finished_at = datetime.now()
                self.repository.mark_iquavis_sync_job_failed(
                    job.Id,
                    finished_at=finished_at,
                    error=str(exc),
                    http_status=exc.status_code,
                )
                self._publish_event(
                    f"iQUAVIS同期に失敗しました: {str(exc)}",
                    True,
                )
            except Exception as exc:
                finished_at = datetime.now()
                self.repository.mark_iquavis_sync_job_failed(
                    job.Id,
                    finished_at=finished_at,
                    error=str(exc),
                    http_status=None,
                )
                self._publish_event(
                    f"iQUAVIS同期に失敗しました: {str(exc)}",
                    True,
                )
        return processed

    def pull_remote_updates(self, *, now: datetime | None = None) -> int:
        if not self.is_enabled():
            return 0

        now = now or datetime.now()
        pulled = 0
        for task in self.repository.list_paired_tasks():
            task_iquavis_id = _effective_iquavis_id(task.IQuavisTaskId) if task else ""
            if not task_iquavis_id:
                continue
            if self.tms_service.is_iquavis_sync_excluded(task.Id):
                continue
            if self.repository.has_open_iquavis_sync_jobs(task.Id):
                continue

            try:
                remote = self.client.get_task(task_iquavis_id, include_progress=True, include_assigns=False)
            except IQuavisRetryableError as exc:
                self._publish_event(f"iQUAVIS取込を一時保留しました: {str(exc)}", True)
                continue
            except IQuavisApiError as exc:
                self._publish_event(f"iQUAVIS取込に失敗しました: {str(exc)}", True)
                continue

            if not self._is_remote_project_allowed(remote.project_id):
                continue

            try:
                if self._apply_remote_task_to_local(task.Id, remote):
                    pulled += 1
                    self._publish_event(f"iQUAVIS取込: {remote.name} を更新しました", False)
                self.repository.mark_task_iquavis_last_pulled(task.Id, now)
            except UserInputError as exc:
                self._publish_event(f"iQUAVIS取込をスキップしました: {str(exc)}", True)

        self.repository.set_state(LAST_PULL_AT_STATE_KEY, now.isoformat())
        return pulled

    def run_background_tick(self, *, now: datetime | None = None) -> dict[str, int]:
        now = now or datetime.now()
        processed = self.process_outbound_jobs(now=now)
        pulled = 0
        editor_synced = 1 if self.run_daily_editor_sync_if_due(now=now) else 0

        last_pull_at_raw = self.repository.get_state(LAST_PULL_AT_STATE_KEY)
        should_pull = True
        if last_pull_at_raw:
            try:
                last_pull_at = datetime.fromisoformat(last_pull_at_raw)
                should_pull = (now - last_pull_at).total_seconds() >= self.settings.iquavis.poll_interval_seconds
            except ValueError:
                should_pull = True

        if should_pull:
            pulled = self.pull_remote_updates(now=now)
        return {"processed": processed, "pulled": pulled, "editor_synced": editor_synced}

    def run_daily_editor_sync_if_due(
        self,
        *,
        now: datetime | None = None,
        force: bool = False,
        adapter: IquavisEditorWorkbookAdapter | None = None,
    ) -> bool:
        now = now or datetime.now()
        workbook_path = self.settings.iquavis.editor_workbook_path
        if workbook_path is None:
            return False
        if not force and not self._is_editor_daily_sync_due(now):
            return False

        try:
            # OOXML書き込み、Excel/VBA保存、保存後の基準再読込を同じ排他区間で実行する。
            with self._editor_workbook_lock:
                self.run_editor_workbook_sync(
                    workbook_path=workbook_path,
                    adapter=adapter,
                    now=now,
                )
                if self.settings.iquavis.excel_server_save_enabled:
                    automation_result = run_excel_server_save(
                        username=self.settings.iquavis.username,
                        password=self.settings.iquavis.password,
                        workbook_path=workbook_path,
                        expected_project_count=(
                            self.settings.iquavis.excel_server_save_expected_project_count
                        ),
                        timeout_seconds=self.settings.iquavis.excel_server_save_timeout_seconds,
                    )
                    # VBAが更新した非表示スナップショットをQOLONY側へ反映する。
                    self.run_editor_workbook_sync(
                        workbook_path=workbook_path,
                        adapter=adapter,
                        now=now,
                    )
                    message, has_warning = format_excel_server_save_event(automation_result)
                    self._publish_event(message, has_warning)
                else:
                    self._publish_event("iquavis_editor定時同期が完了しました", False)
        except ExcelServerSaveError as exc:
            self._publish_event(f"iQUAVIS自動サーバー保存に失敗しました: {str(exc)}", True)
        except Exception as exc:
            self._publish_event(f"iquavis_editor定時同期に失敗しました: {str(exc)}", True)
        finally:
            self.repository.set_state(LAST_EDITOR_DAILY_SYNC_DATE_STATE_KEY, now.date().isoformat())
        return True

    def _is_editor_daily_sync_due(self, now: datetime) -> bool:
        scheduled = now.replace(
            hour=self.settings.iquavis.daily_sync_hour,
            minute=self.settings.iquavis.daily_sync_minute,
            second=0,
            microsecond=0,
        )
        if now < scheduled:
            return False
        last_date = self.repository.get_state(LAST_EDITOR_DAILY_SYNC_DATE_STATE_KEY)
        return last_date != now.date().isoformat()

    def pair_existing_task(self, *, local_task_id: str, iquavis_task_id: str) -> None:
        if not self.is_enabled():
            raise UserInputError("iQUAVIS連携設定が未完了です")

        local_task = self.repository.get_task(local_task_id)
        if local_task is None:
            raise UserInputError("ローカルタスクが見つかりません")
        existing = self.repository.get_task_by_iquavis_task_id(iquavis_task_id)
        if existing is not None and existing.Id != local_task_id:
            raise UserInputError("指定したiQUAVISタスクIDは別のQOLONYタスクに紐付いています")

        remote = self.client.get_task(iquavis_task_id, include_progress=True, include_assigns=False)
        if not self._is_remote_project_allowed(remote.project_id):
            raise UserInputError("許可対象外のiQUAVISプロジェクトです")

        self.repository.set_task_iquavis_link(
            local_task_id,
            iquavis_task_id=remote.id,
            iquavis_project_id=remote.project_id,
        )
        self.repository.mark_task_iquavis_last_pulled(local_task_id, datetime.now())
        self.queue_task_schedule_sync(local_task_id)
        self.queue_task_progress_sync(local_task_id)
        self._publish_event(f"iQUAVISペアリングを設定しました: {local_task.Name}", False)

    def import_remote_tasks(
        self,
        *,
        iquavis_task_ids: list[str],
        creator_user_name: str | None = None,
        default_assignee_name: str | None = None,
    ) -> list[str]:
        if not self.is_enabled():
            raise UserInputError("iQUAVIS連携設定が未完了です")
        if not iquavis_task_ids:
            raise UserInputError("少なくとも1件のiQUAVISタスクIDを指定してください")

        project = self.tms_service.get_project()
        creator = (
            self.repository.get_user_by_name(creator_user_name)
            if creator_user_name
            else self.repository.get_top_manager()
        )
        if creator is None:
            raise UserInputError("作成者ユーザーが見つかりません")

        default_assignee = (
            self.repository.get_user_by_name(default_assignee_name)
            if default_assignee_name
            else creator
        )
        if default_assignee is None:
            raise UserInputError("既定担当ユーザーが見つかりません")

        remote_by_id: dict[str, IQuavisRemoteTask] = {}
        for task_id in dict.fromkeys(iquavis_task_ids):
            remote = self.client.get_task(task_id, include_progress=True, include_assigns=True)
            if not self._is_remote_project_allowed(remote.project_id):
                raise UserInputError(f"許可対象外のiQUAVISプロジェクトです: {remote.project_id}")
            remote_by_id[remote.id] = remote

        ordered_remote_ids = self._order_remote_ids(remote_by_id)
        local_id_by_remote_id: dict[str, str] = {}
        imported_local_ids: list[str] = []

        for remote_id in ordered_remote_ids:
            remote = remote_by_id[remote_id]
            existing = self.repository.get_task_by_iquavis_task_id(remote.id)
            assignee_user_id = self._resolve_local_assignee_id(remote, default_assignee_id=default_assignee.Id)
            local_parent_id = local_id_by_remote_id.get(remote.parent_task_id or "")

            if existing is None:
                created = self.repository.create_task(
                    NewTaskInput(
                        ProjectId=project.Id,
                        ParentTaskId=local_parent_id,
                        Name=remote.name,
                        Type=remote.task_type,
                        AssigneeUserId=assignee_user_id,
                        CreatorUserId=creator.Id,
                        StartDate=remote.start_date,
                        EndDate=remote.end_date,
                        AssignedManHour=0,
                        PlannedDeliverables=[],
                        IsRework=False,
                        Memo=remote.note,
                    )
                )
                task = created
            else:
                self.repository.update_task_import_fields(
                    existing.Id,
                    parent_task_id=local_parent_id,
                    name=remote.name,
                    task_type=remote.task_type,
                    assignee_user_id=assignee_user_id,
                    start_date=remote.start_date,
                    end_date=remote.end_date,
                    memo=remote.note,
                )
                task = self.repository.get_task(existing.Id)
                if task is None:
                    raise UserInputError("取込後のローカルタスク再取得に失敗しました")

            self.repository.set_task_iquavis_link(
                task.Id,
                iquavis_task_id=remote.id,
                iquavis_project_id=remote.project_id,
            )
            self.repository.mark_task_iquavis_last_pulled(task.Id, datetime.now())
            local_id_by_remote_id[remote.id] = task.Id
            imported_local_ids.append(task.Id)

            if remote.status == "Completion":
                self.repository.update_task_status(task.Id, "Completion", 100.0)
            elif remote.status == "Abort":
                self.repository.update_task_status(task.Id, "Abort", 0.0)
            elif remote.status in {"NotStart", "DuringStart"}:
                initial_progress = 0.0 if remote.status == "NotStart" else float(remote.progress_rate or 0)
                self.repository.update_task_status(task.Id, remote.status, initial_progress)

        self.tms_service.recompute_all_task_progress()
        self._publish_event(f"iQUAVISタスクを{len(imported_local_ids)}件取り込みました", False)
        return imported_local_ids

    def import_tasks_from_xlsm(
        self,
        *,
        workbook_path: Path,
        sheet_names: list[str],
        creator_user_name: str | None = None,
        default_assignee_name: str | None = None,
    ) -> SpreadsheetImportResult:
        selection = load_selected_tasks_from_xlsm(workbook_path=workbook_path, sheet_names=sheet_names)
        if not selection.rows:
            raise UserInputError("読み取り対象のタスクがありません")

        project = self.tms_service.get_project()
        creator = (
            self.repository.get_user_by_name(creator_user_name)
            if creator_user_name
            else self.repository.get_top_manager()
        )
        if creator is None:
            raise UserInputError("作成者ユーザーが見つかりません")

        default_assignee = (
            self.repository.get_user_by_name(default_assignee_name)
            if default_assignee_name
            else creator
        )
        if default_assignee is None:
            raise UserInputError("既定担当ユーザーが見つかりません")

        local_id_by_remote_id: dict[str, str] = {}
        created_rows: list[tuple[str, Any]] = []
        imported_local_ids: list[str] = []
        skipped_existing_count = 0

        for row in selection.rows:
            if not self._is_remote_project_allowed(row.project_id):
                raise UserInputError(
                    f"{row.sheet_name} {row.row_number}行目: 許可対象外のiQUAVISプロジェクトです: {row.project_id}"
                )

            existing = self.repository.get_task_by_iquavis_task_id(row.iquavis_task_id)
            if existing is not None:
                local_id_by_remote_id[row.iquavis_task_id] = existing.Id
                skipped_existing_count += 1
                continue

            assignee_user_id = self._resolve_local_assignee_id_fields(
                first_assign_iquavis_user_id=row.first_assign_iquavis_user_id,
                first_assign_login_id=row.first_assign_login_id,
                first_assign_name=row.first_assign_name,
                creator_iquavis_user_id=row.creator_iquavis_user_id,
                creator_login_id=row.creator_login_id,
                creator_name=row.creator_name,
                default_assignee_id=default_assignee.Id,
            )
            created = self.repository.create_task(
                NewTaskInput(
                    ProjectId=project.Id,
                    ParentTaskId=None,
                    Name=row.name,
                    Type=row.task_type,
                    AssigneeUserId=assignee_user_id,
                    CreatorUserId=creator.Id,
                    StartDate=row.start_date,
                    EndDate=row.end_date,
                    AssignedManHour=0,
                    PlannedDeliverables=[],
                    IsRework=False,
                    Memo=row.note,
                )
            )
            self.repository.set_task_iquavis_link(
                created.Id,
                iquavis_task_id=row.iquavis_task_id,
                iquavis_project_id=row.project_id,
            )
            self.repository.mark_task_iquavis_last_pulled(created.Id, datetime.now())
            self._apply_imported_progress_state(
                task_id=created.Id,
                status=row.progress_status,
                progress_rate=row.progress_rate,
            )

            local_id_by_remote_id[row.iquavis_task_id] = created.Id
            imported_local_ids.append(created.Id)
            created_rows.append((created.Id, row))

        for local_task_id, row in created_rows:
            local_parent_id = self._resolve_local_parent_for_import(
                parent_override_local_task_id=row.parent_override_local_task_id,
                remote_parent_task_id=row.remote_parent_task_id,
                local_id_by_remote_id=local_id_by_remote_id,
            )
            if local_parent_id == local_task_id:
                local_parent_id = None
            self.repository.update_task_parent(local_task_id, local_parent_id)

        if imported_local_ids:
            self.tms_service.recompute_all_task_progress()

        self._publish_event(
            f"xlsm取込: 選択{selection.selected_count}件 / 新規{len(imported_local_ids)}件 / 既存スキップ{skipped_existing_count}件",
            False,
        )
        return SpreadsheetImportResult(
            imported_local_ids=imported_local_ids,
            selected_count=selection.selected_count,
            skipped_existing_count=skipped_existing_count,
            duplicate_count=selection.duplicate_count,
        )

    def import_new_tasks_from_editor_snapshot(
        self,
        snapshot: IquavisEditorSnapshot,
        *,
        creator_user_name: str | None = None,
        default_assignee_name: str | None = None,
    ) -> IquavisEditorImportResult:
        importer = IquavisEditorImportService(
            repository=self.repository,
            tms_service=self.tms_service,
        )
        result = importer.import_new_tasks_from_snapshot(
            snapshot,
            creator_user_name=creator_user_name,
            default_assignee_name=default_assignee_name,
        )
        self._publish_event(
            f"iquavis_editor取り込み: domain {len(result.imported_task_domain_ids)}件 / task {len(result.imported_task_ids)}件",
            False,
        )
        return result

    def import_new_tasks_from_editor_workbook(
        self,
        *,
        workbook_path: Path | None = None,
        adapter: IquavisEditorWorkbookAdapter | None = None,
        creator_user_name: str | None = None,
        default_assignee_name: str | None = None,
    ) -> IquavisEditorImportResult:
        resolved_path = workbook_path or self.settings.iquavis.editor_workbook_path
        if resolved_path is None:
            raise UserInputError("iquavis_editor.xlsm のパスが設定されていません")
        workbook_adapter = adapter or OpenpyxlIquavisEditorWorkbookAdapter()
        snapshot = self._with_link_target_targets(
            self._load_editor_snapshot(workbook_adapter, Path(resolved_path))
        )
        return self.import_new_tasks_from_editor_snapshot(
            snapshot,
            creator_user_name=creator_user_name,
            default_assignee_name=default_assignee_name,
        )

    def run_editor_snapshot_sync(
        self,
        snapshot: IquavisEditorSnapshot,
        *,
        workbook_path: Path | None = None,
        adapter: IquavisEditorWorkbookAdapter | None = None,
        now: datetime | None = None,
        workbook_fingerprint: str | None = None,
    ) -> EditorWorkbookSyncResult:
        imported = self.import_new_tasks_from_editor_snapshot(snapshot)
        conflict_result = IquavisEditorConflictSyncService(
            repository=self.repository,
            tms_service=self.tms_service,
        ).sync_snapshot(
            snapshot,
            workbook_path=workbook_path,
            adapter=adapter,
            now=now,
            workbook_fingerprint=workbook_fingerprint,
        )
        if conflict_result.forced_sync_cleared:
            self._clear_link_target_force_sync_requests(conflict_result.forced_sync_cleared)
        return EditorWorkbookSyncResult(imported=imported, conflicts=conflict_result)

    def run_editor_workbook_sync(
        self,
        *,
        workbook_path: Path | None = None,
        adapter: IquavisEditorWorkbookAdapter | None = None,
        now: datetime | None = None,
    ) -> EditorWorkbookSyncResult:
        # Workbookと競合状態を同じ排他区間で更新し、並行解消による後勝ち上書きを防ぐ。
        with self._editor_workbook_lock:
            return self._run_editor_workbook_sync_unlocked(
                workbook_path=workbook_path,
                adapter=adapter,
                now=now,
            )

    def _run_editor_workbook_sync_unlocked(
        self,
        *,
        workbook_path: Path | None = None,
        adapter: IquavisEditorWorkbookAdapter | None = None,
        now: datetime | None = None,
    ) -> EditorWorkbookSyncResult:
        resolved_path = workbook_path or self.settings.iquavis.editor_workbook_path
        if resolved_path is None:
            raise UserInputError("iquavis_editor.xlsm のパスが設定されていません")
        workbook_adapter = adapter or OpenpyxlIquavisEditorWorkbookAdapter()
        resolved_path = Path(resolved_path)
        workbook_fingerprint = self._workbook_fingerprint(resolved_path)
        snapshot = self._without_excluded_targets(
            self._with_link_target_targets(self._load_editor_snapshot(workbook_adapter, resolved_path))
        )
        return self.run_editor_snapshot_sync(
            snapshot,
            workbook_path=resolved_path,
            adapter=workbook_adapter,
            now=now,
            workbook_fingerprint=workbook_fingerprint,
        )

    def list_editor_conflicts(self) -> dict[str, Any]:
        conflicts = parse_conflict_state(self.repository.get_state(IQUAVIS_TASK_CONFLICT_STATE_KEY))
        items: list[dict[str, Any]] = []
        for task_id, conflict in conflicts.items():
            # 旧V1の詳細なしレコード誤判定を含むため、再構築されるまでUIへ出さない
            if conflict.sync_state in {IQUAVIS_CONFLICT_STATE, IQUAVIS_CONFLICT_HOLD_STATE} and not conflict.fields:
                continue
            task = self.repository.get_task(task_id)
            if task is None:
                continue
            fields = [
                {
                    "field": field_name,
                    "baseline": conflict.fields[field_name].baseline,
                    "qolony": conflict.fields[field_name].qolony,
                    "excel": (
                        conflict.fields[field_name].qolony
                        if conflict.sync_state == IQUAVIS_PENDING_SAVE_STATE
                        else conflict.fields[field_name].excel
                    ),
                }
                for field_name in SUPPORTED_SYNC_FIELDS
                if field_name in conflict.fields
            ]
            items.append(
                {
                    "task_id": task_id,
                    "iquavis_id": conflict.iquavis_id,
                    "task_name": task.Name,
                    "sync_state": conflict.sync_state,
                    "message": conflict.message,
                    "recorded_at": conflict.recorded_at.isoformat(),
                    "workbook_fingerprint": conflict.workbook_fingerprint,
                    "fields": fields,
                }
            )
        def _conflict_order(item: dict[str, Any]) -> tuple[int, str, str]:
            sync_state = item["sync_state"]
            if sync_state in {IQUAVIS_CONFLICT_STATE, IQUAVIS_CONFLICT_HOLD_STATE}:
                return (0, item["task_name"], item["task_id"])
            return (1, item["task_name"], item["task_id"])

        items.sort(key=_conflict_order)
        return {"items": items, "count": len(items)}

    def refresh_editor_conflicts(
        self,
        *,
        operator_user_id: str,
        adapter: IquavisEditorWorkbookAdapter | None = None,
    ) -> dict[str, Any]:
        operator = self.repository.get_user(operator_user_id)
        if operator is None or not getattr(operator, "IsActive", True):
            raise IquavisConflictPermissionError("有効な実行ユーザーが見つかりません")
        result = self.run_editor_workbook_sync(adapter=adapter)
        listed = self.list_editor_conflicts()
        return {
            "status": "ok",
            "conflict_count": sum(
                1 for item in listed["items"] if item["sync_state"] == IQUAVIS_CONFLICT_STATE
            ),
            "hold_count": sum(
                1 for item in listed["items"] if item["sync_state"] == IQUAVIS_CONFLICT_HOLD_STATE
            ),
            "pending_count": sum(
                1 for item in listed["items"] if item["sync_state"] == IQUAVIS_PENDING_SAVE_STATE
            ),
            "local_updates_written": len(result.conflicts.local_updates_written),
            "remote_updates_applied": len(result.conflicts.remote_updates_applied),
            "imported_count": len(result.imported.imported_task_ids),
        }

    def preview_editor_authoritative_import(
        self,
        *,
        operator_user_id: str,
        ignored_iquavis_ids: list[str] | None = None,
        ignored_review_item_keys: list[str] | None = None,
        adapter: IquavisEditorWorkbookAdapter | None = None,
    ) -> dict[str, Any]:
        """hiddenシートから、iQUAVISの取得成功値を差分なしで確認する。"""
        with self._editor_workbook_lock:
            self._require_top_manager(operator_user_id)
            workbook_path = self._resolve_editor_workbook_path()
            workbook_fingerprint = self._workbook_fingerprint(workbook_path)
            workbook_adapter = adapter or OpenpyxlIquavisEditorWorkbookAdapter()
            raw_snapshot = self._with_link_target_targets(
                self._load_editor_snapshot(workbook_adapter, workbook_path)
            )
            ignored_ids = {
                ignored_id
                for ignored_id in (_effective_iquavis_id(ignored_id) for ignored_id in (ignored_iquavis_ids or []))
                if ignored_id
            }
            normalized_review_item_keys = self._normalize_review_item_keys(ignored_review_item_keys)
            record_iquavis_ids = {
                _effective_iquavis_id(record.iquavis_id)
                for record in raw_snapshot.task_sync_records
                if record.kind != "task_domain" and _effective_iquavis_id(record.iquavis_id)
            }
            requested_ignored_ids = sorted(ignored_ids & record_iquavis_ids)
            snapshot = self._without_excluded_targets(
                raw_snapshot,
                ignored_iquavis_ids=requested_ignored_ids,
            )
        snapshot = self._filter_authoritative_task_sync_records(
            snapshot,
            ignored_iquavis_ids=requested_ignored_ids,
            ignored_review_item_keys=normalized_review_item_keys,
        )
        if self._workbook_fingerprint(workbook_path) != workbook_fingerprint:
            raise IquavisConflictStaleError(
                "Excelの読み取り中にブックが更新されました。Excelを閉じた状態で、もう一度事前確認してください"
            )
        return self._build_authoritative_import_preview(
            snapshot,
            workbook_path=workbook_path,
            workbook_fingerprint=workbook_fingerprint,
            excluded_by_review=bool(requested_ignored_ids or normalized_review_item_keys),
        )

    def apply_editor_authoritative_import(
        self,
        *,
        operator_user_id: str,
        expected_workbook_fingerprint: str,
        confirmation: str,
        ignored_iquavis_ids: list[str] | None = None,
        ignored_review_item_keys: list[str] | None = None,
        adapter: IquavisEditorWorkbookAdapter | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """hiddenシートから、iQUAVIS取得成功値を通常の検証を通さずQOLONYへ反映する。"""
        with self._editor_workbook_lock:
            self._require_top_manager(operator_user_id)
            if confirmation.strip() != IQUAVIS_AUTHORITATIVE_IMPORT_CONFIRMATION:
                raise UserInputError("iQUAVIS全件反映の確認文字列が一致しません")

            workbook_path = self._resolve_editor_workbook_path()
            workbook_fingerprint = self._workbook_fingerprint(workbook_path)
            if not expected_workbook_fingerprint.strip():
                raise IquavisConflictStaleError("事前確認の結果がありません。もう一度、事前確認を実行してください")
            if workbook_fingerprint != expected_workbook_fingerprint.strip():
                raise IquavisConflictStaleError(
                    "事前確認後にExcelが更新されています。Excelを閉じた状態で、もう一度事前確認してください"
                )

            workbook_adapter = adapter or OpenpyxlIquavisEditorWorkbookAdapter()
            raw_snapshot = self._with_link_target_targets(
                self._load_editor_snapshot(workbook_adapter, workbook_path)
            )
            ignored_ids = {
                ignored_id
                for ignored_id in (_effective_iquavis_id(ignored_id) for ignored_id in (ignored_iquavis_ids or []))
                if ignored_id
            }
            normalized_review_item_keys = self._normalize_review_item_keys(ignored_review_item_keys)
            record_iquavis_ids = {
                _effective_iquavis_id(record.iquavis_id)
                for record in raw_snapshot.task_sync_records
                if record.kind != "task_domain" and _effective_iquavis_id(record.iquavis_id)
            }
            requested_ignored_ids = sorted(ignored_ids & record_iquavis_ids)
            editor_force_sync_ids = {
                record.iquavis_id
                for record in raw_snapshot.task_sync_records
                if record.force_sync is not None and _effective_iquavis_id(record.iquavis_id)
            } | {
                target.iquavis_id
                for target in raw_snapshot.synced_targets
                if target.force_sync is not None and _effective_iquavis_id(target.iquavis_id)
            }
            link_force_sync_ids = {
                target.iquavis_id
                for target in self._read_link_target_targets()
                if target.force_sync is not None and _effective_iquavis_id(target.iquavis_id)
            }
            snapshot = self._without_excluded_targets(
                raw_snapshot,
                ignored_iquavis_ids=requested_ignored_ids,
            )
            snapshot = self._filter_authoritative_task_sync_records(
                snapshot,
                ignored_iquavis_ids=requested_ignored_ids,
                ignored_review_item_keys=normalized_review_item_keys,
            )
            if self._workbook_fingerprint(workbook_path) != workbook_fingerprint:
                raise IquavisConflictStaleError(
                    "Excelの読み取り中にブックが更新されました。Excelを閉じた状態で、もう一度事前確認してください"
                )
            preview = self._build_authoritative_import_preview(
                snapshot,
                workbook_path=workbook_path,
                workbook_fingerprint=workbook_fingerprint,
                excluded_by_review=bool(requested_ignored_ids or normalized_review_item_keys),
            )
            if not preview["ready"]:
                detail = preview["blockers"][0] if preview["blockers"] else "Excelの事前確認に失敗しました"
                raise UserInputError(detail)

            now = now or datetime.now()
            canonical_snapshot = self._authoritative_snapshot(snapshot)
            preflight_updates, _, _ = self._build_authoritative_task_updates(
                canonical_snapshot,
                pulled_at=now,
            )
            self._validate_task_import_parent_graph(preflight_updates)
            backup_path = self._backup_database_before_authoritative_import(now=now)
            restore_updates = self._authoritative_excel_restore_updates(snapshot)
            if restore_updates:
                restore_result = workbook_adapter.restore_iquavis_values(workbook_path, restore_updates)
                requested_restore_ids = {update.iquavis_id for update in restore_updates}
                missing_restore_ids = sorted(requested_restore_ids - set(restore_result.updated_ids))
                if missing_restore_ids:
                    raise UserInputError(
                        f"Excel表示値をiQUAVIS取得成功値へ戻せませんでした: {missing_restore_ids[0]}"
                    )
            editor_force_sync_ids &= {
                record.iquavis_id
                for record in snapshot.task_sync_records
                if _effective_iquavis_id(record.iquavis_id)
            } | {
                target.iquavis_id
                for target in snapshot.synced_targets
                if _effective_iquavis_id(target.iquavis_id)
            }
            link_force_sync_ids &= {
                record.iquavis_id
                for record in snapshot.task_sync_records
                if _effective_iquavis_id(record.iquavis_id)
            } | {
                target.iquavis_id
                for target in snapshot.synced_targets
                if _effective_iquavis_id(target.iquavis_id)
            }
            if editor_force_sync_ids:
                clear_result = workbook_adapter.clear_force_sync_requests(
                    workbook_path,
                    sorted(editor_force_sync_ids),
                )
                missing_cleared_ids = sorted(
                    editor_force_sync_ids - set(clear_result.cleared_force_sync_ids)
                )
                if missing_cleared_ids:
                    raise UserInputError(
                        "Excelの強制同期指定をクリアできませんでした: "
                        f"{missing_cleared_ids[0]}。バックアップを確認してください: {backup_path}"
                    )
            if link_force_sync_ids:
                cleared_link_ids = set(
                    self._clear_link_target_force_sync_requests(sorted(link_force_sync_ids))
                )
                missing_link_ids = sorted(link_force_sync_ids - cleared_link_ids)
                if missing_link_ids:
                    raise UserInputError(
                        "リンク対象ブックの強制同期指定をクリアできませんでした: "
                        f"{missing_link_ids[0]}。バックアップを確認してください: {backup_path}"
                    )
            imported = self.import_new_tasks_from_editor_snapshot(canonical_snapshot)
            if imported.missing_target_ids:
                raise UserInputError(
                    "親またはタスクドメインを解決できないiQUAVISタスクがあります: "
                    f"{imported.missing_target_ids[0]}。DBバックアップ: {backup_path}"
                )
            updates, changed_task_ids, update_warnings = self._build_authoritative_task_updates(
                canonical_snapshot,
                pulled_at=now,
            )
            self._validate_task_import_parent_graph(updates)
            self.repository.apply_task_import_updates(updates)
            self.tms_service.recompute_all_task_progress()

            affected_task_ids = {update.task_id for update in updates}
            stored = parse_conflict_state(self.repository.get_state(IQUAVIS_TASK_CONFLICT_STATE_KEY))
            cleared_state_count = sum(1 for task_id in affected_task_ids if task_id in stored)
            remaining = {
                task_id: conflict
                for task_id, conflict in stored.items()
                if task_id not in affected_task_ids
            }
            final_workbook_fingerprint = self._workbook_fingerprint(workbook_path)
            derived_conflicts = self._authoritative_derived_conflicts(
                canonical_snapshot,
                recorded_at=now,
                workbook_fingerprint=final_workbook_fingerprint,
            )
            remaining.update(derived_conflicts)
            self.repository.set_state(IQUAVIS_TASK_CONFLICT_STATE_KEY, serialize_conflict_state(remaining))

            derived_progress_mismatches = [
                conflict.iquavis_id
                for conflict in derived_conflicts.values()
                if "progress_rate" in conflict.fields
            ]
            warnings = [*preview["warnings"], *update_warnings]
            if derived_progress_mismatches:
                warnings.append(
                    "iQUAVISの進捗率とQOLONY再計算値が一致しないタスクがあります。"
                    "QOLONYは完了状態、実績工数、見積工数、子タスクから進捗率を計算します。"
                )
            warnings = list(dict.fromkeys(warnings))
            self._publish_event(
                "iQUAVIS全件採用: "
                f"新規task {len(imported.imported_task_ids)}件 / "
                f"更新 {len(changed_task_ids)}件 / "
                f"進捗差 {len(derived_progress_mismatches)}件",
                False,
            )
            return {
                "status": "ok",
                "imported_task_count": len(imported.imported_task_ids),
                "imported_domain_count": len(imported.imported_task_domain_ids),
                "updated_task_count": len(changed_task_ids),
                "cleared_state_count": cleared_state_count,
                "derived_progress_mismatch_count": len(derived_progress_mismatches),
                "protected_derived_conflict_count": len(derived_conflicts),
                "backup_path": str(backup_path),
                "restored_excel_task_count": len(restore_updates),
                "warnings": warnings,
            }

    # ------------------------------------------------------------------
    # xlsm(iQUAVIS)を正とした強制取込
    # ------------------------------------------------------------------

    def _stored_baselines(self) -> dict[str, IquavisTaskSyncProperties]:
        """QOLONYが保存した「前回iQUAVISと合意した値」を読み出す。"""
        result: dict[str, IquavisTaskSyncProperties] = {}
        for row in self.repository.list_iquavis_task_baselines():
            result[row.TaskId] = IquavisTaskSyncProperties(
                name=(row.Name or "").strip(),
                start_date=row.StartDate,
                end_date=row.EndDate,
                assigned_minutes=0,
                actual_minutes=int(row.ActualManHour or 0),
                status=(row.Status or "").strip(),
                progress_rate=row.ProgressRate,
                memo=(row.Memo or "").strip(),
            )
        return result

    def _iquavis_authoritative_properties(
        self,
        record: IquavisTaskSyncRecord,
    ) -> IquavisTaskSyncProperties:
        """iQUAVISの正となる値を返す。

        非表示 `__TASKS_...` の取得/保存成功値を使う。表示シートはQOLONYが黄色で
        書き込んだ下書きを含むため、そのままではiQUAVISの値として使えない。
        """
        return record.previous if record.previous is not None else record.iquavis_current

    def _workbook_value_matches(
        self,
        record: IquavisTaskSyncRecord,
        field_name: str,
        expected: Any,
    ) -> bool:
        """競合検出時に記録したiQUAVIS値が、いまもブック上にあるかを確認する。

        表示シートと非表示シートのどちらかに一致すれば、検出後に変化したとは見なさない。
        通常の差分同期は表示シートを、強制取込は非表示シート（取得成功値）をiQUAVIS値として
        記録するため、両方を許容しないと採択できない競合が生まれる。
        """
        if _sync_json_value(getattr(record.iquavis_current, field_name)) == expected:
            return True
        if record.previous is not None and _sync_json_value(getattr(record.previous, field_name)) == expected:
            return True
        return False

    def _conflict_adoption_properties(
        self,
        record: IquavisTaskSyncRecord,
        *,
        conflict: IquavisTaskConflict,
        field_names: tuple[str, ...],
    ) -> IquavisTaskSyncProperties:
        """iQUAVIS採択で実際に書き込む値を選ぶ。

        競合パネルに表示した値をそのまま採用する。表示シートが一致しない場合だけ、
        非表示シートの取得成功値を使う。
        """
        if all(
            _sync_json_value(getattr(record.iquavis_current, field_name))
            == conflict.fields[field_name].excel
            for field_name in field_names
            if field_name in conflict.fields
        ):
            return record.iquavis_current
        if record.previous is not None:
            return record.previous
        return record.iquavis_current

    def _keeps_unprocessed_conflict(
        self,
        conflict: IquavisTaskConflict,
        *,
        task_id: str,
        excluded_task_ids: set[str],
        workbook_fingerprint: str,
    ) -> bool:
        """今回評価しなかったタスクの競合・保存待ちを残すかどうかを決める。

        残しても解決できない状態は捨てる。同期対象外タスクと、検出時のブックが
        現行ブックと一致しないレコードは、採択しようとしても陳腐化エラーになるだけで、
        画面上は消せない滞留として積み上がる。
        """
        if task_id in excluded_task_ids:
            return False
        if conflict.workbook_fingerprint and conflict.workbook_fingerprint != workbook_fingerprint:
            return False
        return True

    def _capture_task_baseline(
        self,
        *,
        task_id: str,
        iquavis_id: str,
        properties: IquavisTaskSyncProperties,
        captured_at: datetime,
    ) -> None:
        self.repository.upsert_iquavis_task_baselines(
            [
                IquavisTaskBaselineInput(
                    task_id=task_id,
                    iquavis_task_id=iquavis_id,
                    name=properties.name,
                    start_date=properties.start_date,
                    end_date=properties.end_date,
                    actual_manhour=int(properties.actual_minutes),
                    status=properties.status,
                    progress_rate=properties.progress_rate,
                    memo=properties.memo,
                )
            ],
            captured_at=captured_at,
        )

    def _build_forced_import_plan(
        self,
        *,
        adapter: IquavisEditorWorkbookAdapter | None = None,
    ) -> _ForcedImportPlan:
        workbook_path = self._resolve_editor_workbook_path()
        workbook_fingerprint = self._workbook_fingerprint(workbook_path)
        workbook_adapter = adapter or OpenpyxlIquavisEditorWorkbookAdapter()
        raw_snapshot = self._with_link_target_targets(
            self._load_editor_snapshot(workbook_adapter, workbook_path)
        )
        snapshot = self._without_excluded_targets(raw_snapshot)
        excluded_record_count = max(
            0, len(raw_snapshot.task_sync_records) - len(snapshot.task_sync_records)
        )

        project = self.tms_service.get_project()
        tasks = self.repository.list_tasks(project.Id)
        conflict_service = IquavisEditorConflictSyncService(
            repository=self.repository,
            tms_service=self.tms_service,
        )
        local_by_task_id, parent_task_ids = conflict_service.local_sync_properties(tasks=tasks)
        task_by_iquavis_id: dict[str, Task] = {}
        duplicate_local_link_ids: list[str] = []
        for task in tasks:
            iquavis_id = _effective_iquavis_id(task.IQuavisTaskId)
            if not iquavis_id:
                continue
            if iquavis_id in task_by_iquavis_id:
                duplicate_local_link_ids.append(iquavis_id)
                continue
            task_by_iquavis_id[iquavis_id] = task
        baseline_by_task_id = self._stored_baselines()

        decisions: list[ForcedTaskDecision] = []
        iquavis_props_by_task_id: dict[str, IquavisTaskSyncProperties] = {}
        new_task_iquavis_ids: list[str] = []
        missing_workbook_baseline_count = 0
        missing_qolony_baseline_count = 0
        seen_iquavis_ids: set[str] = set()

        for record in snapshot.task_sync_records:
            iquavis_id = _effective_iquavis_id(record.iquavis_id)
            if not iquavis_id or record.kind == "task_domain":
                continue
            if iquavis_id in seen_iquavis_ids:
                continue
            seen_iquavis_ids.add(iquavis_id)
            if record.previous is None:
                missing_workbook_baseline_count += 1
            iquavis_properties = self._iquavis_authoritative_properties(record)

            task = task_by_iquavis_id.get(iquavis_id)
            if task is None:
                new_task_iquavis_ids.append(iquavis_id)
                continue
            local = local_by_task_id.get(task.Id)
            if local is None:
                continue
            baseline = baseline_by_task_id.get(task.Id)
            if baseline is None:
                missing_qolony_baseline_count += 1
            decisions.append(
                classify_forced_import_task(
                    task_id=task.Id,
                    iquavis_id=iquavis_id,
                    qolony=local,
                    iquavis=iquavis_properties,
                    baseline=baseline,
                    is_parent=task.Id in parent_task_ids,
                )
            )
            iquavis_props_by_task_id[task.Id] = iquavis_properties

        local_only_task_count = sum(
            1 for task in tasks if not _effective_iquavis_id(task.IQuavisTaskId)
        )
        return _ForcedImportPlan(
            workbook_path=workbook_path,
            workbook_fingerprint=workbook_fingerprint,
            snapshot=snapshot,
            decisions=tuple(decisions),
            iquavis_props_by_task_id=iquavis_props_by_task_id,
            new_task_iquavis_ids=tuple(new_task_iquavis_ids),
            local_only_task_count=local_only_task_count,
            missing_qolony_baseline_count=missing_qolony_baseline_count,
            missing_workbook_baseline_count=missing_workbook_baseline_count,
            excluded_record_count=excluded_record_count,
            duplicate_local_link_ids=tuple(dict.fromkeys(duplicate_local_link_ids)),
        )

    def preview_editor_forced_import(
        self,
        *,
        operator_user_id: str,
        adapter: IquavisEditorWorkbookAdapter | None = None,
    ) -> dict[str, Any]:
        """強制取込で何件が更新・競合になるかを、DBを変更せずに確認する。"""
        with self._editor_workbook_lock:
            self._require_top_manager(operator_user_id)
            plan = self._build_forced_import_plan(adapter=adapter)
            if self._workbook_fingerprint(plan.workbook_path) != plan.workbook_fingerprint:
                raise IquavisConflictStaleError(
                    "Excelの読み取り中にブックが更新されました。Excelを閉じた状態で、もう一度確認してください。"
                )
        return plan.to_preview()

    def apply_editor_forced_import(
        self,
        *,
        operator_user_id: str,
        expected_workbook_fingerprint: str,
        confirmation: str,
        adapter: IquavisEditorWorkbookAdapter | None = None,
        creator_user_name: str | None = None,
        default_assignee_name: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """競合以外をiQUAVIS値でQOLONYへ強制的に上書きする。

        この処理はxlsmへ一切書き込まない。QOLONY専有タスクも削除しない。
        """
        with self._editor_workbook_lock:
            self._require_top_manager(operator_user_id)
            if confirmation.strip() != IQUAVIS_FORCED_IMPORT_CONFIRMATION:
                raise UserInputError("強制取込の確認文字列が一致しません。")

            workbook_path = self._resolve_editor_workbook_path()
            workbook_fingerprint = self._workbook_fingerprint(workbook_path)
            if not expected_workbook_fingerprint.strip():
                raise IquavisConflictStaleError(
                    "事前確認の結果がありません。先に事前確認を実行してください。"
                )
            if workbook_fingerprint != expected_workbook_fingerprint.strip():
                raise IquavisConflictStaleError(
                    "事前確認のあとにExcelが更新されています。Excelを閉じた状態で、もう一度事前確認してください。"
                )

            now = now or datetime.now()
            backup_path = self._backup_database_before_authoritative_import(now=now)

            workbook_adapter = adapter or OpenpyxlIquavisEditorWorkbookAdapter()
            # iQUAVIS側にしか無いタスクを先に取り込み、そのあと同じ基準で全件を評価する。
            preview_plan = self._build_forced_import_plan(adapter=workbook_adapter)
            imported = self.import_new_tasks_from_editor_snapshot(
                preview_plan.snapshot,
                creator_user_name=creator_user_name,
                default_assignee_name=default_assignee_name,
            )
            plan = self._build_forced_import_plan(adapter=workbook_adapter)

            updates: list[TaskImportUpdate] = []
            baselines: list[IquavisTaskBaselineInput] = []
            conflict_decisions: list[ForcedTaskDecision] = []
            # 1万件規模でも1クエリで済ませる。1件ずつ引くとCAD端末で数十秒かかる。
            project = self.tms_service.get_project()
            task_by_id = {task.Id: task for task in self.repository.list_tasks(project.Id)}
            for decision in plan.decisions:
                if decision.is_conflict:
                    conflict_decisions.append(decision)
                    continue
                iquavis_properties = plan.iquavis_props_by_task_id.get(decision.task_id)
                if iquavis_properties is None:
                    continue
                task = task_by_id.get(decision.task_id)
                if task is None:
                    continue
                if decision.has_adoptions:
                    updates.append(
                        self._forced_task_import_update(
                            task=task,
                            decision=decision,
                            iquavis=iquavis_properties,
                            pulled_at=now,
                        )
                    )
                baselines.append(
                    IquavisTaskBaselineInput(
                        task_id=decision.task_id,
                        iquavis_task_id=decision.iquavis_id,
                        name=iquavis_properties.name,
                        start_date=iquavis_properties.start_date,
                        end_date=iquavis_properties.end_date,
                        actual_manhour=int(iquavis_properties.actual_minutes),
                        status=iquavis_properties.status,
                        progress_rate=iquavis_properties.progress_rate,
                        memo=iquavis_properties.memo,
                    )
                )

            if updates:
                self._validate_task_import_parent_graph(updates)
                self.repository.apply_task_import_updates(updates)
            self.tms_service.recompute_all_task_progress()
            self.repository.upsert_iquavis_task_baselines(baselines, captured_at=now)

            processed_task_ids = {decision.task_id for decision in plan.decisions}
            excluded_task_ids = self.tms_service.get_iquavis_sync_excluded_task_ids()
            stored = parse_conflict_state(self.repository.get_state(IQUAVIS_TASK_CONFLICT_STATE_KEY))
            remaining = {
                task_id: conflict
                for task_id, conflict in stored.items()
                if task_id not in processed_task_ids
                and self._keeps_unprocessed_conflict(
                    conflict,
                    task_id=task_id,
                    excluded_task_ids=excluded_task_ids,
                    workbook_fingerprint=plan.workbook_fingerprint,
                )
            }
            for decision in conflict_decisions:
                remaining[decision.task_id] = IquavisTaskConflict(
                    task_id=decision.task_id,
                    iquavis_id=decision.iquavis_id,
                    message=IQUAVIS_FORCED_CONFLICT_MESSAGE,
                    recorded_at=now,
                    fields={
                        item.field: IquavisTaskConflictField(
                            field=item.field,
                            baseline=item.baseline,
                            qolony=item.qolony,
                            excel=item.iquavis,
                        )
                        for item in decision.conflict_fields
                    },
                    pending_fields={},
                    workbook_fingerprint=plan.workbook_fingerprint,
                    sync_state=IQUAVIS_CONFLICT_STATE,
                )
            self.repository.set_state(
                IQUAVIS_TASK_CONFLICT_STATE_KEY, serialize_conflict_state(remaining)
            )

            cleared_state_count = sum(
                1
                for task_id in processed_task_ids
                if task_id in stored and task_id not in remaining
            )
            self._publish_event(
                "iQUAVIS強制取込: "
                f"新規 {len(imported.imported_task_ids)}件 / "
                f"更新 {len(updates)}件 / "
                f"競合 {len(conflict_decisions)}件",
                False,
            )
            return {
                "status": "ok",
                "imported_task_count": len(imported.imported_task_ids),
                "imported_domain_count": len(imported.imported_task_domain_ids),
                "updated_task_count": len(updates),
                "conflict_task_count": len(conflict_decisions),
                "baseline_task_count": len(baselines),
                "cleared_state_count": cleared_state_count,
                "local_only_task_count": plan.local_only_task_count,
                "backup_path": str(backup_path),
                "workbook_fingerprint": plan.workbook_fingerprint,
            }

    def _forced_task_import_update(
        self,
        *,
        task: Task,
        decision: ForcedTaskDecision,
        iquavis: IquavisTaskSyncProperties,
        pulled_at: datetime,
    ) -> TaskImportUpdate:
        """採用対象の項目だけをiQUAVIS値に差し替えた更新行を作る。

        親タスクID・担当者・見積MHはQOLONYローカルの情報なので触らない。
        """
        adopted = {item.field for item in decision.adopt_fields}
        status = iquavis.status if "status" in adopted else task.Status
        progress_rate = float(task.ProgressRate)
        if "status" in adopted:
            # 進捗率は直後の再計算で確定するが、完了/未着手は先に整合させておく。
            if status == "Completion":
                progress_rate = 100.0
            elif status in {"NotStart", "Abort"}:
                progress_rate = 0.0
        return TaskImportUpdate(
            task_id=task.Id,
            parent_task_id=task.ParentTaskId,
            name=iquavis.name if "name" in adopted else task.Name,
            task_type=task.Type,
            assignee_user_id=task.AssigneeUserId,
            start_date=iquavis.start_date if "start_date" in adopted else task.StartDate,
            end_date=iquavis.end_date if "end_date" in adopted else task.EndDate,
            actual_manhour=(
                int(iquavis.actual_minutes) if "actual_minutes" in adopted else int(task.ActualManHour)
            ),
            status=status,
            progress_rate=progress_rate,
            memo=iquavis.memo if "memo" in adopted else (task.Memo or ""),
            pulled_at=pulled_at,
        )

    def _authoritative_snapshot(self, snapshot: IquavisEditorSnapshot) -> IquavisEditorSnapshot:
        """hiddenシートから取得成功値を初期整合で採用するcanonical値にする。"""
        baseline_by_id = {
            record.iquavis_id: record.previous
            for record in snapshot.task_sync_records
            if record.kind != "task_domain"
            and _effective_iquavis_id(record.iquavis_id)
            and record.previous is not None
        }
        tasks = []
        for task in snapshot.tasks:
            task_id = _effective_iquavis_id(task.iquavis_task_id)
            baseline = baseline_by_id.get(task_id) if task_id else None
            if baseline is None:
                tasks.append(task)
                continue
            tasks.append(
                replace(
                    task,
                    name=baseline.name,
                    start_date=baseline.start_date,
                    end_date=baseline.end_date,
                    assigned_minutes=baseline.assigned_minutes,
                    note=baseline.memo,
                    status=baseline.status,
                    progress_rate=baseline.progress_rate,
                )
            )
        records = [
            replace(record, iquavis_current=record.previous)
            if record.kind != "task_domain"
            and record.previous is not None
            and _effective_iquavis_id(record.iquavis_id)
            else record
            for record in snapshot.task_sync_records
        ]
        return replace(snapshot, tasks=tasks, task_sync_records=records)

    def _authoritative_excel_restore_updates(
        self,
        snapshot: IquavisEditorSnapshot,
    ) -> list[IquavisEditorQolonyUpdate]:
        """visible draftをhidden取得成功値へ戻す更新だけを準備する。"""
        updates: list[IquavisEditorQolonyUpdate] = []
        # 見積工数は通常のQOLONY→Excel同期対象ではないが、初期整合では
        # staleなセルを残すと後日の「サーバーへ保存」で送信するため、復元用に保持する。
        writable_fields = {*SUPPORTED_SYNC_FIELDS, "assigned_minutes"}
        for record in snapshot.task_sync_records:
            if (
                record.kind == "task_domain"
                or record.previous is None
                or not _effective_iquavis_id(record.iquavis_id)
            ):
                continue
            field_names = tuple(
                field_name
                for field_name in IQUAVIS_AUTHORITATIVE_COMPARE_FIELDS
                if field_name in writable_fields
                and getattr(record.previous, field_name) != getattr(record.iquavis_current, field_name)
            )
            if not field_names:
                continue
            updates.append(
                IquavisEditorQolonyUpdate(
                    iquavis_id=record.iquavis_id,
                    properties=record.previous,
                    field_names=field_names,
                )
            )
        return updates

    def _backup_database_before_authoritative_import(self, *, now: datetime) -> Path:
        """初期整合更新前にSQLiteオンラインバックアップを作成する。"""
        session_factory = getattr(self.repository, "_session_factory", None)
        bind = getattr(session_factory, "kw", {}).get("bind") if session_factory is not None else None
        database = getattr(getattr(bind, "url", None), "database", None)
        if not database:
            raise UserInputError("初期整合前バックアップの実DBパスを特定できません")
        source_path = Path(database).resolve()
        if not source_path.exists():
            raise UserInputError(f"初期整合前バックアップ元DBが見つかりません: {source_path}")
        timestamp = now.strftime("%Y%m%d_%H%M%S")
        # バックアップ名は元DBのファイル名から作る（例: qolony.db → qolony.pre_iquavis_reconcile_...db）。
        stem = source_path.stem
        backup_path = source_path.parent / f"{stem}.pre_iquavis_reconcile_{timestamp}.db"
        suffix = 2
        while backup_path.exists():
            backup_path = source_path.parent / f"{stem}.pre_iquavis_reconcile_{timestamp}_{suffix}.db"
            suffix += 1
        try:
            with sqlite3.connect(source_path) as source_connection, sqlite3.connect(backup_path) as backup_connection:
                source_connection.backup(backup_connection)
        except sqlite3.Error as exc:
            if backup_path.exists():
                backup_path.unlink()
            raise UserInputError(f"初期整合前のDBバックアップに失敗しました: {exc}") from exc
        return backup_path

    def _build_authoritative_import_preview(
        self,
        snapshot: IquavisEditorSnapshot,
        *,
        workbook_path: Path,
        workbook_fingerprint: str,
        excluded_by_review: bool = False,
    ) -> dict[str, Any]:
        records: list[IquavisTaskSyncRecord] = []
        indexed_records: list[tuple[int, IquavisTaskSyncRecord, str]] = []
        blank_review_rows: list[tuple[int, IquavisTaskSyncRecord]] = []
        record_rows_by_id: dict[str, list[int]] = {}
        ignored_blank_record_count = 0
        for row_index, record in enumerate(snapshot.task_sync_records):
            if record.kind == "task_domain":
                continue
            iquavis_id = _effective_iquavis_id(record.iquavis_id)
            if not iquavis_id:
                ignored_blank_record_count += 1
                blank_review_rows.append((row_index, record))
                continue
            indexed_records.append((row_index, record, iquavis_id))
            record_rows_by_id.setdefault(iquavis_id, []).append(row_index)
            records.append(record)
        dirty_ids: list[str] = []
        semantic_dirty_ids: list[str] = []
        dirty_details: list[dict[str, Any]] = []
        missing_baseline_ids: list[str] = []
        missing_baseline_details: list[dict[str, Any]] = []
        unknown_status_ids: list[str] = []
        unknown_status_details: list[dict[str, Any]] = []
        invalid_schedule_ids: list[str] = []
        review_items: list[dict[str, Any]] = []
        reviewed_items: set[tuple[str, str, str]] = set()
        status_counts: Counter[str] = Counter()

        def add_review_item(
            iquavis_id: str,
            kind: str,
            reason: str,
            task_name: str | None = None,
            review_item_key: str | None = None,
        ) -> None:
            normalized = _effective_iquavis_id(iquavis_id)
            review_key = review_item_key or (f"id:{normalized}" if normalized else None)
            if not review_key:
                return
            key = (review_key, kind, reason)
            if key in reviewed_items:
                return
            reviewed_items.add(key)
            review_items.append(
                {
                    "iquavis_id": normalized,
                    "task_name": task_name,
                    "kind": kind,
                    "reason": reason,
                    "review_item_key": review_key,
                }
            )
        for row_index, record in blank_review_rows:
            add_review_item(
                iquavis_id="",
                kind="missing_iquavis_id",
                reason="iQUAVIS IDが空の行は初期整合対象外として除外しました",
                task_name=record.iquavis_current.name,
                review_item_key=self._build_authoritative_record_review_key(row_index),
            )
        record_counts = Counter(
            _effective_iquavis_id(record.iquavis_id) for record in records
        )
        duplicate_record_ids = sorted(iquavis_id for iquavis_id, count in record_counts.items() if count > 1)
        record_name_by_id = {
            _effective_iquavis_id(record.iquavis_id): record.iquavis_current.name
            for record in records
            if _effective_iquavis_id(record.iquavis_id)
        }
        for row_index, record, _ in indexed_records:
            if record.previous is None:
                missing_baseline_ids.append(record.iquavis_id)
                add_review_item(
                    record.iquavis_id,
                    kind="missing_baseline",
                    reason="前回iQUAVIS取得/保存成功値がないため、差分比較ができません",
                    task_name=record.iquavis_current.name,
                    review_item_key=self._build_authoritative_record_review_key(row_index),
                )
                if len(missing_baseline_details) < IQUAVIS_AUTHORITATIVE_DETAIL_LIMIT:
                    missing_baseline_details.append(
                        {
                            "iquavis_id": record.iquavis_id,
                            "task_name": record.iquavis_current.name,
                        }
                    )
                authoritative = record.iquavis_current
            else:
                authoritative = record.previous
            changed_fields = []
            if record.previous is not None:
                changed_fields = [
                    field_name
                    for field_name in IQUAVIS_AUTHORITATIVE_COMPARE_FIELDS
                    if getattr(record.previous, field_name) != getattr(record.iquavis_current, field_name)
                ]
            semantic_changed_fields = [
                field_name
                for field_name in changed_fields
                if not _authoritative_values_equal(
                    field_name,
                    getattr(record.previous, field_name),
                    getattr(record.iquavis_current, field_name),
                )
            ] if record.previous is not None else []
            if semantic_changed_fields:
                semantic_dirty_ids.append(record.iquavis_id)
            if changed_fields:
                dirty_ids.append(record.iquavis_id)
                if len(dirty_details) < IQUAVIS_AUTHORITATIVE_DETAIL_LIMIT:
                    dirty_details.append(
                        {
                            "iquavis_id": record.iquavis_id,
                            "task_name": authoritative.name,
                            "fields": changed_fields,
                        }
                    )
            status = authoritative.status.strip()
            if status:
                status_counts[status] += 1
            if status not in QOLONY_TASK_STATUSES:
                unknown_status_ids.append(record.iquavis_id)
                add_review_item(
                    record.iquavis_id,
                    kind="unknown_status",
                    reason=f"iQUAVISステータスが未対応です: {status}",
                    task_name=authoritative.name,
                    review_item_key=self._build_authoritative_record_review_key(row_index),
                )
                if len(unknown_status_details) < IQUAVIS_AUTHORITATIVE_DETAIL_LIMIT:
                    unknown_status_details.append(
                        {
                            "iquavis_id": record.iquavis_id,
                            "task_name": authoritative.name,
                            "status": status,
                        }
                    )
            if authoritative.start_date > authoritative.end_date:
                invalid_schedule_ids.append(record.iquavis_id)
                add_review_item(
                    record.iquavis_id,
                    kind="invalid_schedule",
                    reason=f"開始日が期限日より後です: {record.iquavis_id}",
                    task_name=authoritative.name,
                    review_item_key=self._build_authoritative_record_review_key(row_index),
                )

        project = self.tms_service.get_project()
        local_tasks = self.repository.list_tasks(project.Id)
        local_link_counts = Counter(
            _effective_iquavis_id(task.IQuavisTaskId)
            for task in local_tasks
            if _effective_iquavis_id(task.IQuavisTaskId)
        )
        duplicate_local_link_ids = sorted(
            iquavis_id
            for iquavis_id, count in local_link_counts.items()
            if count > 1
        )
        local_by_iquavis_id = {
            _effective_iquavis_id(task.IQuavisTaskId): task
            for task in local_tasks
            if _effective_iquavis_id(task.IQuavisTaskId)
        }
        local_by_id = {task.Id: task for task in local_tasks}
        target_by_id = {
            _effective_iquavis_id(target.iquavis_id): target
            for target in snapshot.synced_targets
            if _effective_iquavis_id(target.iquavis_id)
        }

        def local_task_for(iquavis_id: str) -> Task | None:
            linked = local_by_iquavis_id.get(iquavis_id)
            if linked is not None:
                return linked
            target = target_by_id.get(iquavis_id)
            if target is None or not target.qolony_task_id:
                return None
            return self.repository.get_task(target.qolony_task_id)

        conflict_service = IquavisEditorConflictSyncService(
            repository=self.repository,
            tms_service=self.tms_service,
        )
        local_properties, _ = conflict_service._local_properties_context(tasks=local_tasks)
        changed_task_ids: set[str] = set()
        changed_field_count = 0
        existing_task_count = 0
        for record in records:
            local_task = local_task_for(_effective_iquavis_id(record.iquavis_id))
            if local_task is None:
                continue
            existing_task_count += 1
            local = local_properties.get(local_task.Id, conflict_service._local_properties(local_task))
            authoritative = record.previous or record.iquavis_current
            changes = sum(
                1
                for field_name in SUPPORTED_SYNC_FIELDS
                if getattr(local, field_name) != getattr(authoritative, field_name)
                and not (
                    field_name == "progress_rate"
                    and authoritative.progress_rate is None
                )
            )
            if changes:
                changed_task_ids.add(local_task.Id)
                changed_field_count += changes

        snapshot_task_ids = {
            _effective_iquavis_id(task.iquavis_task_id)
            for task in snapshot.tasks
            if _effective_iquavis_id(task.iquavis_task_id)
        }
        snapshot_domain_ids = {
            _effective_iquavis_id(domain.iquavis_task_domain_id)
            for domain in snapshot.task_domains
            if _effective_iquavis_id(domain.iquavis_task_domain_id)
        }
        new_task_count = sum(1 for task_id in snapshot_task_ids if local_task_for(task_id) is None)
        new_domain_count = sum(1 for domain_id in snapshot_domain_ids if local_task_for(domain_id) is None)
        explicitly_linked_local_ids = {
            target.qolony_task_id
            for target in snapshot.synced_targets
            if target.qolony_task_id
        }
        local_only_task_count = sum(
            1
            for task in local_tasks
            if not _effective_iquavis_id(task.IQuavisTaskId) and task.Id not in explicitly_linked_local_ids
        )
        excluded_task_count = len(self.tms_service.get_iquavis_sync_excluded_task_ids())

        def local_parent_identity(task: Task) -> str | None:
            if task.ParentTaskId is None:
                return None
            parent = local_by_id.get(task.ParentTaskId)
            if parent is None:
                return f"qolony:{task.ParentTaskId}"
            parent_id = _effective_iquavis_id(parent.IQuavisTaskId)
            return parent_id or f"qolony:{parent.Id}"

        preserved_local_parent_count = 0
        for domain in snapshot.task_domains:
            domain_id = _effective_iquavis_id(domain.iquavis_task_domain_id)
            if not domain_id:
                continue
            current = local_task_for(domain_id)
            target = target_by_id.get(domain_id)
            if current is None or (target is not None and target.qolony_parent_task_id):
                continue
            if local_parent_identity(current) != domain.parent_task_domain_id:
                preserved_local_parent_count += 1
        for task_record in snapshot.tasks:
            task_id = _effective_iquavis_id(task_record.iquavis_task_id)
            if not task_id:
                continue
            current = local_task_for(task_id)
            target = target_by_id.get(task_id)
            if current is None or (target is not None and target.qolony_parent_task_id):
                continue
            remote_parent_id = task_record.parent_task_id or task_record.task_domain_id
            if local_parent_identity(current) != remote_parent_id:
                preserved_local_parent_count += 1

        structure_errors = self._authoritative_structure_errors(snapshot)

        blockers: list[str] = []
        if not records and not excluded_by_review:
            if ignored_blank_record_count == 0:
                blockers.append(
                    "Excelの全タスク表示値を読み取れません。対象プロジェクトで「タスクを書き出し」または「タスク作成」を実行してください。"
                )
        if duplicate_record_ids:
            blockers.append(
                f"同じiQUAVIS IDのタスクが重複しています: {len(duplicate_record_ids)}件。Excelの全件取得で再確認してください。"
            )
        if duplicate_local_link_ids:
            blockers.append(
                f"同じiQUAVIS IDに複数のQOLONYタスクがリンクされています: {len(duplicate_local_link_ids)}件。個別にリンクを修正してください。"
            )
        if missing_baseline_ids:
            blockers.append(
                f"取得/保存成功値がないタスクが{len(missing_baseline_ids)}件あります。Excelで全件取得を再確認してください。"
            )
        if unknown_status_ids:
            blockers.append(
                f"未対応iQUAVIS状態のタスクが{len(unknown_status_ids)}件あります。対応状態は NotStart / DuringStart / Completion / Abort です。"
            )
            blockers.append(
                f"着手日が期限より後のタスクが{len(invalid_schedule_ids)}件あります。iQUAVIS側の日付を修正してください。"
            )
        if structure_errors:
            blockers.append(
                f"iQUAVISの親・タスクドメイン・同期対象を解決できない行が{len(structure_errors)}件あります。"
                "詳細を確認し、Excelで全件取得をやり直してください。"
        )

        for iquavis_id in duplicate_record_ids:
            for row_index in record_rows_by_id.get(iquavis_id, []):
                add_review_item(
                    iquavis_id=iquavis_id,
                    kind="duplicate_remote_record",
                    reason="iQUAVISのIDが重複しています",
                    task_name=record_name_by_id.get(iquavis_id),
                    review_item_key=self._build_authoritative_record_review_key(row_index),
                )
        for iquavis_id in duplicate_local_link_ids:
            local_task = local_by_iquavis_id.get(iquavis_id)
            rows = record_rows_by_id.get(iquavis_id, [])
            if rows:
                for row_index in rows:
                    add_review_item(
                        iquavis_id=iquavis_id,
                        kind="duplicate_local_link",
                        reason="QOLONY上で同じiQUAVIS IDが複数タスクにリンクしているため上書き対象が不明です",
                        task_name=local_task.Name if local_task is not None else None,
                        review_item_key=self._build_authoritative_record_review_key(row_index),
                    )
            else:
                add_review_item(
                    iquavis_id=iquavis_id,
                    kind="duplicate_local_link",
                    reason="QOLONY上で同じiQUAVIS IDが複数タスクにリンクしているため上書き対象が不明です",
                    task_name=local_task.Name if local_task is not None else None,
                    review_item_key=f"id:{iquavis_id}",
                )
        for error in structure_errors:
            error_id = _effective_iquavis_id(error["iquavis_id"])
            rows = record_rows_by_id.get(error_id, [])
            if rows:
                for row_index in rows:
                    add_review_item(
                        iquavis_id=error["iquavis_id"],
                        kind=f"structure:{error['kind']}",
                        reason=error["reason"],
                        task_name=error.get("task_name"),
                        review_item_key=self._build_authoritative_record_review_key(row_index),
                    )
            else:
                add_review_item(
                    iquavis_id=error["iquavis_id"],
                    kind=f"structure:{error['kind']}",
                    reason=error["reason"],
                    task_name=error.get("task_name"),
                    review_item_key=f"id:{error_id}" if error_id else None,
                )

        review_items.sort(
            key=lambda item: (
                {
                    "unknown_status": 0,
                    "invalid_schedule": 1,
                    "missing_baseline": 2,
                    "duplicate_remote_record": 3,
                    "duplicate_local_link": 4,
                    "structure:task": 5,
                    "structure:task_domain": 6,
                    "structure:qolony_task": 7,
                    "structure:unknown": 8,
                }.get(item["kind"], 99),
                item["kind"],
                item["iquavis_id"],
            )
        )

        warnings: list[str] = []
        if ignored_blank_record_count:
            warnings.append(
                f"iQUAVIS IDが空の同期行が{ignored_blank_record_count}件あるため、初回整合では対象外として除外しました。"
            )
        if dirty_ids:
            warnings.append(
                f"Excel表示値と非表示シートの取得成功値が異なるタスクが{len(dirty_ids)}件あります。"
                "初期整合では表示値を破棄し、非表示シートの取得成功値を採用します。"
            )
        warnings.extend(
            [
                "実行直前に運用DBのバックアップを同じフォルダへ自動作成します。",
                "見積MHはQOLONY管理のため、iQUAVIS値では上書きしません。",
                "進捗率はQOLONYで再計算されます。iQUAVISの進捗率が0%でも状態がCompletionであれば100%になります。",
                "タスク種別・担当者・親タスク・タスクドメインは、直前に全件取得したExcel表示構造を使います。",
            ]
        )
        return {
            "ready": not blockers,
            "workbook_fingerprint": workbook_fingerprint,
            "workbook_name": workbook_path.name,
            "scanned_task_count": len(records),
            "ignored_blank_record_count": ignored_blank_record_count,
            "existing_task_count": existing_task_count,
            "new_task_count": new_task_count,
            "new_domain_count": new_domain_count,
            "local_only_task_count": local_only_task_count,
            "excluded_task_count": excluded_task_count,
            "preserved_local_parent_count": preserved_local_parent_count,
            "changed_task_count": len(changed_task_ids),
            "changed_field_count": changed_field_count,
            "dirty_excel_task_count": len(dirty_ids) + len(missing_baseline_ids),
            "excel_snapshot_difference_count": len(dirty_ids),
            "semantic_excel_snapshot_difference_count": len(semantic_dirty_ids),
            "excel_snapshot_difference_details": dirty_details,
            "missing_baseline_task_count": len(missing_baseline_ids),
            "missing_baseline_details": missing_baseline_details,
            "status_breakdown": dict(sorted(status_counts.items())),
            "unknown_status_count": len(unknown_status_ids),
            "unknown_status_details": unknown_status_details,
            "unsupported_status_count": len(unknown_status_ids),
            "duplicate_iquavis_id_count": len(duplicate_record_ids),
            "duplicate_local_link_count": len(duplicate_local_link_ids),
            "duplicate_local_link_ids": duplicate_local_link_ids[:IQUAVIS_AUTHORITATIVE_DETAIL_LIMIT],
            "structure_error_count": len(structure_errors),
            "structure_error_details": structure_errors[:IQUAVIS_AUTHORITATIVE_DETAIL_LIMIT],
            "review_items": review_items[:IQUAVIS_AUTHORITATIVE_DETAIL_LIMIT],
            "blockers": blockers,
            "warnings": warnings,
        }

    def _authoritative_structure_errors(
        self,
        snapshot: IquavisEditorSnapshot,
    ) -> list[dict[str, str]]:
        """新規取込前に、対象IDと親子グラフを副作用なしで検証する。"""
        errors: list[dict[str, str]] = []
        seen_errors: set[tuple[str, str, str]] = set()

        def add(iquavis_id: str, kind: str, reason: str) -> None:
            key = (iquavis_id, kind, reason)
            if key in seen_errors:
                return
            seen_errors.add(key)
            errors.append(
                {
                    "iquavis_id": iquavis_id,
                    "kind": kind,
                    "reason": reason,
                }
            )

        task_counts = Counter(
            _effective_iquavis_id(task.iquavis_task_id)
            for task in snapshot.tasks
            if _effective_iquavis_id(task.iquavis_task_id)
        )
        domain_counts = Counter(
            _effective_iquavis_id(domain.iquavis_task_domain_id)
            for domain in snapshot.task_domains
            if _effective_iquavis_id(domain.iquavis_task_domain_id)
        )
        task_by_id = {
            _effective_iquavis_id(task.iquavis_task_id): task
            for task in snapshot.tasks
            if _effective_iquavis_id(task.iquavis_task_id)
        }
        domain_by_id = {
            _effective_iquavis_id(domain.iquavis_task_domain_id): domain
            for domain in snapshot.task_domains
            if _effective_iquavis_id(domain.iquavis_task_domain_id)
        }
        target_by_id = {
            _effective_iquavis_id(target.iquavis_id): target
            for target in snapshot.synced_targets
            if _effective_iquavis_id(target.iquavis_id)
        }
        record_ids = {
            _effective_iquavis_id(record.iquavis_id)
            for record in snapshot.task_sync_records
            if record.kind != "task_domain" and _effective_iquavis_id(record.iquavis_id)
        }
        project = self.tms_service.get_project()
        local_tasks = self.repository.list_tasks(project.Id)
        local_by_id = {task.Id: task for task in local_tasks}
        local_by_iquavis_id = {
            _effective_iquavis_id(task.IQuavisTaskId): task
            for task in local_tasks
            if _effective_iquavis_id(task.IQuavisTaskId)
        }

        for iquavis_id, count in task_counts.items():
            if count > 1:
                add(iquavis_id, "task", "同じiQUAVIS IDの行が複数あります")
        for iquavis_id, count in domain_counts.items():
            if count > 1:
                add(iquavis_id, "task_domain", "同じTask Domain IDの行が複数あります")
        for iquavis_id in sorted(set(task_by_id) & set(domain_by_id)):
            add(iquavis_id, "unknown", "TaskとTask Domainで同じIDが使われています")

        explicit_link_counts = Counter(
            target.qolony_task_id
            for target in target_by_id.values()
            if target.qolony_task_id
        )
        for local_task_id, count in explicit_link_counts.items():
            if count <= 1:
                continue
            add(
                local_task_id,
                "qolony_task",
                "複数のiQUAVIS IDが同じQOLONYタスクへ明示リンクされています",
            )

        active_task_ids: set[str] = set()
        active_domain_ids: set[str] = set()
        for iquavis_id, target in target_by_id.items():
            if target.qolony_task_id and target.qolony_task_id not in local_by_id:
                add(iquavis_id, target.kind, f"QOLONYリンク先が見つかりません: {target.qolony_task_id}")
            elif target.qolony_task_id:
                linked_local = local_by_id[target.qolony_task_id]
                linked_local_id = _effective_iquavis_id(linked_local.IQuavisTaskId)
                if linked_local_id and linked_local_id != iquavis_id:
                    add(
                        iquavis_id,
                        target.kind,
                        f"QOLONYリンク先は別のiQUAVIS IDへリンク済みです: {linked_local_id}",
                    )
            if target.qolony_parent_task_id and target.qolony_parent_task_id not in local_by_id:
                add(iquavis_id, target.kind, f"QOLONY親タスクが見つかりません: {target.qolony_parent_task_id}")
            if iquavis_id in task_by_id:
                active_task_ids.add(iquavis_id)
            elif iquavis_id in domain_by_id:
                active_domain_ids.add(iquavis_id)
            else:
                add(iquavis_id, target.kind, "同期対象IDが全タスク取得結果にありません")

        for task_id in sorted(active_task_ids):
            task = task_by_id[task_id]
            target = target_by_id[task_id]
            if task_id not in record_ids:
                add(task_id, "task", "前回iQUAVIS取得成功値がありません")
            if target.qolony_parent_task_id:
                continue
            parent_id = _effective_iquavis_id(task.parent_task_id)
            domain_id = _effective_iquavis_id(task.task_domain_id)
            if parent_id:
                if parent_id not in local_by_iquavis_id:
                    if parent_id not in task_by_id:
                        add(task_id, "task", f"親タスクが全タスク取得結果にありません: {parent_id}")
                    elif parent_id not in active_task_ids:
                        add(task_id, "task", f"親タスクが同期対象に含まれていません: {parent_id}")
            elif domain_id and domain_id not in domain_by_id and domain_id not in local_by_iquavis_id:
                add(task_id, "task", f"task domainが見つかりません: {domain_id}")

        required_domain_ids = set(active_domain_ids)
        required_domain_ids.update(
            domain_id
            for task_id in active_task_ids
            if (domain_id := _effective_iquavis_id(task_by_id[task_id].task_domain_id))
            if not target_by_id[task_id].qolony_parent_task_id
            and not task_by_id[task_id].parent_task_id
        )
        for origin_id in sorted(required_domain_ids):
            cursor: str | None = _effective_iquavis_id(origin_id)
            visiting: set[str] = set()
            while cursor:
                if cursor in visiting:
                    add(origin_id, "task_domain", f"task domainの親子関係が循環しています: {cursor}")
                    break
                visiting.add(cursor)
                domain = domain_by_id.get(cursor)
                if domain is None:
                    if cursor not in local_by_iquavis_id:
                        add(origin_id, "task_domain", f"task domainが全タスク取得結果にありません: {cursor}")
                    break
                cursor = _effective_iquavis_id(domain.parent_task_domain_id)

        for origin_id in sorted(active_task_ids):
            cursor: str | None = origin_id
            visiting: set[str] = set()
            while cursor and cursor in task_by_id:
                if cursor in visiting:
                    add(origin_id, "task", f"親タスク関係が循環しています: {cursor}")
                    break
                visiting.add(cursor)
                target = target_by_id.get(cursor)
                if target is not None and target.qolony_parent_task_id:
                    break
                parent_id = _effective_iquavis_id(task_by_id[cursor].parent_task_id)
                if not parent_id or parent_id in local_by_iquavis_id:
                    break
                cursor = parent_id

        return errors

    def _build_authoritative_task_updates(
        self,
        snapshot: IquavisEditorSnapshot,
        *,
        pulled_at: datetime,
    ) -> tuple[list[TaskImportUpdate], set[str], list[str]]:
        project = self.tms_service.get_project()
        local_tasks = self.repository.list_tasks(project.Id)
        local_by_iquavis_id = {
            _effective_iquavis_id(task.IQuavisTaskId): task
            for task in local_tasks
            if _effective_iquavis_id(task.IQuavisTaskId)
        }
        target_by_id = {
            _effective_iquavis_id(target.iquavis_id): target
            for target in snapshot.synced_targets
            if _effective_iquavis_id(target.iquavis_id)
        }
        record_by_id = {
            _effective_iquavis_id(record.iquavis_id): record
            for record in snapshot.task_sync_records
            if record.kind != "task_domain" and _effective_iquavis_id(record.iquavis_id)
        }
        import_service = IquavisEditorImportService(
            repository=self.repository,
            tms_service=self.tms_service,
        )
        warnings: list[str] = []
        updates: dict[str, TaskImportUpdate] = {}
        changed_task_ids: set[str] = set()

        def existing_parent_id(iquavis_id: str, current: Task) -> str | None:
            target = target_by_id.get(iquavis_id)
            if target is None or not target.qolony_parent_task_id:
                return current.ParentTaskId
            parent = self.repository.get_task(target.qolony_parent_task_id)
            if parent is None:
                raise UserInputError(f"QOLONY親タスクが見つかりません: {target.qolony_parent_task_id}")
            return parent.Id

        for domain in snapshot.task_domains:
            domain_id = _effective_iquavis_id(domain.iquavis_task_domain_id)
            if not domain_id:
                continue
            current = local_by_iquavis_id.get(domain_id)
            if current is None:
                continue
            if domain_id not in target_by_id:
                # 同期除外サブツリーはsynced_targetsとtask_domainsの親子解決のため除外対象。
                continue
            parent_id = existing_parent_id(domain_id, current)
            assignee_id = import_service._resolve_active_local_user_id(domain.assignee) or current.AssigneeUserId
            update = TaskImportUpdate(
                task_id=current.Id,
                parent_task_id=parent_id,
                name=domain.name.strip(),
                task_type="GroupTask",
                assignee_user_id=assignee_id,
                # タスクドメイン出力には日程がなく、readerの日付読込日を仮値として利用
                start_date=current.StartDate,
                end_date=current.EndDate,
                actual_manhour=current.ActualManHour,
                memo=domain.note.strip(),
                status=current.Status,
                progress_rate=current.ProgressRate,
                pulled_at=pulled_at,
            )
            updates[current.Id] = update
            if self._task_import_update_changed(current, update):
                changed_task_ids.add(current.Id)

        for task_record in snapshot.tasks:
            task_id = _effective_iquavis_id(task_record.iquavis_task_id)
            if not task_id:
                continue
            current = local_by_iquavis_id.get(task_id)
            sync_record = record_by_id.get(task_id)
            if current is None or sync_record is None:
                continue
            properties = sync_record.previous or sync_record.iquavis_current
            parent_id = existing_parent_id(task_id, current)
            resolved_assignee_id = import_service._resolve_active_local_user_id(task_record.assignee)
            if resolved_assignee_id is None and task_record.assignee is not None:
                warnings.append(
                    f"iQUAVISユーザーとQOLONYユーザーの対応付けができませんでした: {task_record.name}"
                )
            update = TaskImportUpdate(
                task_id=current.Id,
                parent_task_id=parent_id,
                name=properties.name.strip(),
                task_type=task_record.task_type.strip() or "Task",
                assignee_user_id=resolved_assignee_id or current.AssigneeUserId,
                start_date=properties.start_date,
                end_date=properties.end_date,
                actual_manhour=max(0, int(properties.actual_minutes)),
                status=properties.status,
                progress_rate=float(properties.progress_rate or 0),
                memo=properties.memo.strip(),
                pulled_at=pulled_at,
            )
            updates[current.Id] = update
            if self._task_import_update_changed(current, update):
                changed_task_ids.add(current.Id)

        return list(updates.values()), changed_task_ids, warnings

    def _task_import_update_changed(self, task: Task, update: TaskImportUpdate) -> bool:
        return any(
            (
                task.ParentTaskId != update.parent_task_id,
                task.Name != update.name,
                task.Type != update.task_type,
                task.AssigneeUserId != update.assignee_user_id,
                task.StartDate != update.start_date,
                task.EndDate != update.end_date,
                task.ActualManHour != update.actual_manhour,
                task.Status != update.status,
                abs(float(task.ProgressRate) - float(update.progress_rate)) > 1e-9,
                (task.Memo or "").strip() != update.memo,
            )
        )

    def _validate_task_import_parent_graph(self, updates: list[TaskImportUpdate]) -> None:
        project = self.tms_service.get_project()
        parent_by_id = {
            task.Id: task.ParentTaskId
            for task in self.repository.list_tasks(project.Id)
        }
        parent_by_id.update({update.task_id: update.parent_task_id for update in updates})
        for task_id in parent_by_id:
            seen: set[str] = set()
            cursor: str | None = task_id
            while cursor is not None and cursor in parent_by_id:
                if cursor in seen:
                    raise UserInputError("iQUAVIS取込後の親子関係が循環するため、全件反映を中止しました")
                seen.add(cursor)
                cursor = parent_by_id[cursor]

    def _authoritative_derived_conflicts(
        self,
        snapshot: IquavisEditorSnapshot,
        *,
        recorded_at: datetime,
        workbook_fingerprint: str,
    ) -> dict[str, IquavisTaskConflict]:
        """iQUAVIS値を構造上そのまま保持できない場合に競合として扱う。"""
        conflict_service = IquavisEditorConflictSyncService(
            repository=self.repository,
            tms_service=self.tms_service,
        )
        local_properties, parent_task_ids = conflict_service._local_properties_context()
        project = self.tms_service.get_project()
        local_by_iquavis_id = {
            _effective_iquavis_id(task.IQuavisTaskId): task
            for task in self.repository.list_tasks(project.Id)
            if _effective_iquavis_id(task.IQuavisTaskId)
        }
        conflicts: dict[str, IquavisTaskConflict] = {}
        for record in snapshot.task_sync_records:
            record_id = _effective_iquavis_id(record.iquavis_id)
            task = local_by_iquavis_id.get(record_id)
            local = local_properties.get(task.Id) if task is not None else None
            if local is None:
                continue
            fields: dict[str, IquavisTaskConflictField] = {}
            for field_name in conflict_service._qolony_owned_sync_fields(task.Id, parent_task_ids):
                remote_value = getattr(record.iquavis_current, field_name)
                if field_name == "progress_rate" and remote_value is None:
                    continue
                # 未計測の進捗率はiQUAVISの値を消すだけなので、差異として立てない。
                if field_name == "progress_rate" and is_unmeasured_progress_rate(local):
                    continue
                local_value = getattr(local, field_name)
                if local_value == remote_value:
                    continue
                fields[field_name] = IquavisTaskConflictField(
                    field=field_name,
                    baseline=_sync_json_value(remote_value),
                    qolony=_sync_json_value(local_value),
                    excel=_sync_json_value(remote_value),
                )
            if not fields:
                continue
            conflicts[task.Id] = IquavisTaskConflict(
                task_id=task.Id,
                iquavis_id=record_id,
                message=IQUAVIS_DERIVED_CONFLICT_MESSAGE,
                recorded_at=recorded_at,
                fields=fields,
                pending_fields={},
                workbook_fingerprint=workbook_fingerprint,
                sync_state=IQUAVIS_CONFLICT_STATE,
            )
        return conflicts

    def _require_top_manager(self, operator_user_id: str) -> None:
        operator = self.repository.get_user(operator_user_id)
        if operator is None or not getattr(operator, "IsActive", True):
            raise IquavisConflictPermissionError("有効な実行ユーザーが見つかりません")
        if not operator.IsTopManager:
            raise IquavisConflictPermissionError("iQUAVIS全件反映はTop Managerのみ実行できます")

    def _resolve_editor_workbook_path(self) -> Path:
        workbook_path = self.settings.iquavis.editor_workbook_path
        if workbook_path is None:
            raise UserInputError("iquavis_editor.xlsm のパスが設定されていません")
        resolved_path = Path(workbook_path)
        if not resolved_path.is_absolute():
            resolved_path = (Path(__file__).resolve().parents[2] / resolved_path).resolve()
        if not resolved_path.exists():
            raise UserInputError(f"iquavis_editor.xlsm が見つかりません: {resolved_path}")
        return resolved_path

    def _load_editor_snapshot(
        self,
        adapter: IquavisEditorWorkbookAdapter,
        workbook_path: Path,
    ) -> IquavisEditorSnapshot:
        try:
            return adapter.load_snapshot(workbook_path)
        except IquavisEditorWorkbookError as exc:
            raise UserInputError(f"Excelの同期データを読み取れません: {exc}") from exc

    def resolve_editor_conflict(
        self,
        *,
        task_id: str,
        operator_user_id: str,
        direction: str,
        expected_recorded_at: str,
        adapter: IquavisEditorWorkbookAdapter | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        with self._editor_workbook_lock:
            return self._resolve_editor_conflict_unlocked(
                task_id=task_id,
                operator_user_id=operator_user_id,
                direction=direction,
                expected_recorded_at=expected_recorded_at,
                adapter=adapter,
                now=now,
            )

    def set_editor_conflict_hold(
        self,
        *,
        task_id: str,
        operator_user_id: str,
        hold: bool,
        adapter: IquavisEditorWorkbookAdapter | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        with self._editor_workbook_lock:
            return self._set_editor_conflict_hold_unlocked(
                task_id=task_id,
                operator_user_id=operator_user_id,
                hold=hold,
                adapter=adapter,
                now=now,
            )

    def _set_editor_conflict_hold_unlocked(
        self,
        *,
        task_id: str,
        operator_user_id: str,
        hold: bool,
        adapter: IquavisEditorWorkbookAdapter | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if not task_id:
            raise UserInputError("競合対象タスクIDが指定されていません")
        task = self.repository.get_task(task_id)
        if task is None:
            raise UserInputError("競合対象タスクが見つかりません")
        operator = self.repository.get_user(operator_user_id)
        if operator is None or not getattr(operator, "IsActive", True):
            raise IquavisConflictPermissionError("有効な実行ユーザーが見つかりません")
        if task.AssigneeUserId != operator.Id and not operator.IsTopManager:
            raise IquavisConflictPermissionError("競合対象タスクの担当者またはTop Managerのみ実行できます")

        now = now or datetime.now()
        conflicts = parse_conflict_state(self.repository.get_state(IQUAVIS_TASK_CONFLICT_STATE_KEY))
        conflict = conflicts.get(task_id)
        if conflict is None:
            raise UserInputError("競合対象の状態が見つかりません")
        stored_state = None
        try:
            raw_state_payload = json.loads(self.repository.get_state(IQUAVIS_TASK_CONFLICT_STATE_KEY) or "{}")
            raw_state_item = raw_state_payload.get(task_id, {}) if isinstance(raw_state_payload, dict) else {}
            stored_state = (
                str(raw_state_item.get("sync_state", "")).strip() if isinstance(raw_state_item, dict) else None
            )
        except Exception:
            stored_state = None

        if hold:
            if stored_state and stored_state != IQUAVIS_CONFLICT_STATE:
                raise UserInputError("保留できる状態は競合タスクのみです")
            if conflict.sync_state == IQUAVIS_CONFLICT_HOLD_STATE:
                return {
                    "status": "ok",
                    "task_id": task_id,
                    "sync_state": IQUAVIS_CONFLICT_HOLD_STATE,
                    "recorded_at": conflict.recorded_at.isoformat(),
                }
            if conflict.sync_state != IQUAVIS_CONFLICT_STATE:
                raise UserInputError("保留できる状態は競合タスクのみです")

            conflicts[task_id] = replace(
                conflict,
                sync_state=IQUAVIS_CONFLICT_HOLD_STATE,
                recorded_at=now,
            )
            self.repository.set_state(IQUAVIS_TASK_CONFLICT_STATE_KEY, serialize_conflict_state(conflicts))
            return {
                "status": "ok",
                "task_id": task_id,
                "sync_state": IQUAVIS_CONFLICT_HOLD_STATE,
                "recorded_at": now.isoformat(),
            }

        if conflict.sync_state != IQUAVIS_CONFLICT_HOLD_STATE:
            return {
                "status": "ok",
                "task_id": task_id,
                "sync_state": conflict.sync_state,
                "recorded_at": conflict.recorded_at.isoformat(),
            }

        workbook_path = self.settings.iquavis.editor_workbook_path
        if workbook_path is None:
            raise UserInputError("iquavis_editor.xlsm のパスが設定されていません")

        removed = dict(conflicts)
        removed.pop(task_id, None)
        self.repository.set_state(IQUAVIS_TASK_CONFLICT_STATE_KEY, serialize_conflict_state(removed))
        result = self._run_editor_workbook_sync_unlocked(
            workbook_path=Path(workbook_path),
            adapter=adapter or OpenpyxlIquavisEditorWorkbookAdapter(),
            now=now,
        )
        refreshed = parse_conflict_state(self.repository.get_state(IQUAVIS_TASK_CONFLICT_STATE_KEY))
        next_conflict = refreshed.get(task_id)
        return {
            "status": "ok",
            "task_id": task_id,
            "sync_state": next_conflict.sync_state if next_conflict is not None else "Linked",
            "recorded_at": next_conflict.recorded_at.isoformat() if next_conflict is not None else None,
            "local_updates_written": len(result.conflicts.local_updates_written),
            "remote_updates_applied": len(result.conflicts.remote_updates_applied),
            "imported_count": len(result.imported.imported_task_ids),
        }

    def _resolve_editor_conflict_unlocked(
        self,
        *,
        task_id: str,
        operator_user_id: str,
        direction: str,
        expected_recorded_at: str,
        adapter: IquavisEditorWorkbookAdapter | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if direction not in {"qolony_to_iquavis", "iquavis_to_qolony"}:
            raise UserInputError("競合解消の方向が不正です")
        task = self.repository.get_task(task_id)
        if task is None:
            raise UserInputError("競合対象タスクが見つかりません")
        operator = self.repository.get_user(operator_user_id)
        if operator is None or not getattr(operator, "IsActive", True):
            raise IquavisConflictPermissionError("有効な実行ユーザーが見つかりません")
        if task.AssigneeUserId != operator.Id and not operator.IsTopManager:
            raise IquavisConflictPermissionError("競合解消は対象タスクの担当者またはTop Managerのみ実行できます")

        conflicts = parse_conflict_state(self.repository.get_state(IQUAVIS_TASK_CONFLICT_STATE_KEY))
        conflict = conflicts.get(task_id)
        if conflict is None:
            raise UserInputError("解消対象の競合が見つかりません")
        if conflict.sync_state == IQUAVIS_CONFLICT_HOLD_STATE:
            raise UserInputError("保留中の競合は先に解除してください")
        if conflict.sync_state != IQUAVIS_CONFLICT_STATE:
            raise UserInputError("このタスクはiQUAVIS保存待ちです")
        if not conflict.fields:
            raise IquavisConflictStaleError("旧形式の競合です。Excel同期を再実行して差分を更新してください")
        if conflict.recorded_at.isoformat() != expected_recorded_at.strip():
            raise IquavisConflictStaleError("競合情報が更新されています。画面を再読み込みしてください")

        workbook_path = self.settings.iquavis.editor_workbook_path
        if workbook_path is None:
            raise UserInputError("iquavis_editor.xlsm のパスが設定されていません")
        resolved_path = Path(workbook_path)
        if not resolved_path.is_absolute():
            resolved_path = (Path(__file__).resolve().parents[2] / resolved_path).resolve()
        if not resolved_path.exists():
            raise UserInputError(f"iquavis_editor.xlsm が見つかりません: {resolved_path}")
        current_fingerprint = self._workbook_fingerprint(resolved_path)
        if conflict.workbook_fingerprint and current_fingerprint != conflict.workbook_fingerprint:
            raise IquavisConflictStaleError("Excelが競合検出後に更新されています。同期を再実行してください")

        workbook_adapter = adapter or OpenpyxlIquavisEditorWorkbookAdapter()
        snapshot = self._without_excluded_targets(
            self._with_link_target_targets(self._load_editor_snapshot(workbook_adapter, resolved_path))
        )
        conflict_iquavis_id = _effective_iquavis_id(conflict.iquavis_id)
        record = next(
            (
                item
                for item in snapshot.task_sync_records
                if _effective_iquavis_id(item.iquavis_id) == conflict_iquavis_id
            ),
            None,
        )
        if record is None:
            raise IquavisConflictStaleError("Excelに競合対象タスクが見つかりません。同期を再実行してください")

        conflict_service = IquavisEditorConflictSyncService(repository=self.repository, tms_service=self.tms_service)
        self.tms_service.recompute_all_task_progress()
        local_by_task_id, parent_task_ids = conflict_service._local_properties_context()
        local = local_by_task_id.get(task.Id, conflict_service._local_properties(task))
        qolony_owned_fields = set(conflict_service._qolony_owned_sync_fields(task.Id, parent_task_ids))
        derived_conflict = is_iquavis_derived_conflict(conflict)
        protected_fields = set(conflict.fields) & qolony_owned_fields
        if derived_conflict and protected_fields and direction == "iquavis_to_qolony":
            raise UserInputError(
                "QOLONYの親子集計項目はiQUAVIS値で直接上書きできません。"
                "同期対象外にするか親子構造を調整するか、QOLONY値を採用してください"
            )
        for field_name, values in conflict.fields.items():
            if field_name not in SUPPORTED_SYNC_FIELDS:
                raise IquavisConflictStaleError("未対応項目を含む競合です。同期を再実行してください")
            if field_name in qolony_owned_fields and not derived_conflict:
                raise IquavisConflictStaleError("QOLONY再計算項目の旧競合です。同期を再実行してください")
            if _sync_json_value(getattr(local, field_name)) != values.qolony:
                raise IquavisConflictStaleError(
                    "QOLONY側の値が競合検出後に更新されています。同期を再実行してください。"
                )
            if not self._workbook_value_matches(record, field_name, values.excel):
                raise IquavisConflictStaleError(
                    "iQUAVIS側の値が競合検出後に更新されています。同期を再実行してください。"
                )
        for field_name, values in conflict.pending_fields.items():
            if field_name not in SUPPORTED_SYNC_FIELDS:
                raise IquavisConflictStaleError("未対応項目を含む競合です。同期を再実行してください")
            if _sync_json_value(getattr(local, field_name)) != values.qolony:
                raise IquavisConflictStaleError("QOLONY側の値が競合検出後に更新されています。同期を再実行してください")
            if _sync_json_value(getattr(record.iquavis_current, field_name)) != values.qolony:
                raise IquavisConflictStaleError("Excel側の自動マージ結果が更新されています。同期を再実行してください")

        now = now or datetime.now()
        conflict_field_names = tuple(field_name for field_name in SUPPORTED_SYNC_FIELDS if field_name in conflict.fields)
        pending_field_names = tuple(field_name for field_name in SUPPORTED_SYNC_FIELDS if field_name in conflict.pending_fields)
        if direction == "iquavis_to_qolony" and any(
            field_name == "status" and str(values.excel) not in QOLONY_TASK_STATUSES
            for field_name, values in conflict.fields.items()
        ):
            raise UserInputError("Excel側の状態はQOLONYで表現できません。QOLONYを採用してください")
        if record.force_sync is not None:
            workbook_adapter.clear_force_sync_requests(resolved_path, [conflict_iquavis_id])
            self._clear_link_target_force_sync_requests([conflict_iquavis_id])
        if direction == "qolony_to_iquavis":
            field_names = tuple(dict.fromkeys([*pending_field_names, *conflict_field_names]))
            write_result = workbook_adapter.write_qolony_updates(
                resolved_path,
                [
                    IquavisEditorQolonyUpdate(
                        iquavis_id=conflict_iquavis_id,
                        properties=local,
                        field_names=field_names,
                    )
                ],
            )
            if conflict_iquavis_id not in write_result.updated_ids:
                raise UserInputError("QOLONY変更のExcel書き出し対象が見つかりません")
            pending = replace(
                conflict,
                message="QOLONYの採用値をExcelへ書き出しました。ExcelからiQUAVISへ保存してください。",
                recorded_at=now,
                fields={
                    field_name: as_written_conflict_field(values)
                    for field_name, values in {**conflict.pending_fields, **conflict.fields}.items()
                },
                pending_fields={},
                sync_state=IQUAVIS_PENDING_SAVE_STATE,
            )
            conflicts[task_id] = pending
            final_fingerprint = self._workbook_fingerprint(resolved_path)
            conflicts = {
                existing_task_id: replace(existing_conflict, workbook_fingerprint=final_fingerprint)
                for existing_task_id, existing_conflict in conflicts.items()
            }
            pending = conflicts[task_id]
            self.repository.set_state(IQUAVIS_TASK_CONFLICT_STATE_KEY, serialize_conflict_state(conflicts))
            return {
                "status": "ok",
                "task_id": task_id,
                "direction": direction,
                "sync_state": IQUAVIS_PENDING_SAVE_STATE,
                "recorded_at": pending.recorded_at.isoformat(),
            }

        adopted_iquavis_properties = self._conflict_adoption_properties(
            record,
            conflict=conflict,
            field_names=conflict_field_names,
        )
        conflict_service._apply_remote_fields(task_id, adopted_iquavis_properties, conflict_field_names)
        self.repository.mark_task_iquavis_last_pulled(task_id, now)
        self.tms_service.recompute_all_task_progress()
        # 採択でQOLONYとiQUAVISが一致したので、次回の3値比較の基準を更新する。
        self._capture_task_baseline(
            task_id=task_id,
            iquavis_id=conflict_iquavis_id,
            properties=adopted_iquavis_properties,
            captured_at=now,
        )

        pending_fields = {
            field_name: as_written_conflict_field(values)
            for field_name, values in conflict.pending_fields.items()
        }
        if pending_fields:
            conflicts[task_id] = replace(
                conflict,
                message="Excel側の値をQOLONYへ反映し、QOLONYの再計算値をExcelへ書き出しました。",
                recorded_at=now,
                fields=pending_fields,
                pending_fields={},
                sync_state=IQUAVIS_PENDING_SAVE_STATE,
            )
        else:
            conflicts.pop(task_id, None)

        affected_task_ids: set[str] = set()
        cursor = self.repository.get_task(task_id)
        while cursor is not None and cursor.Id not in affected_task_ids:
            affected_task_ids.add(cursor.Id)
            cursor = self.repository.get_task(cursor.ParentTaskId) if cursor.ParentTaskId else None

        final_local_by_task_id, final_parent_task_ids = conflict_service._local_properties_context()
        record_by_iquavis_id = {item.iquavis_id: item for item in snapshot.task_sync_records}
        derived_updates: list[IquavisEditorQolonyUpdate] = []
        for affected_task_id in affected_task_ids:
            affected_task = self.repository.get_task(affected_task_id)
            if affected_task is None or not affected_task.IQuavisTaskId:
                continue
            affected_record = record_by_iquavis_id.get(affected_task.IQuavisTaskId)
            local = final_local_by_task_id.get(affected_task_id)
            if affected_record is None or local is None:
                continue
            derived_fields: dict[str, IquavisTaskConflictField] = {}
            for field_name in conflict_service._qolony_owned_sync_fields(
                affected_task_id,
                final_parent_task_ids,
            ):
                remote_value = getattr(affected_record.iquavis_current, field_name)
                if field_name == "progress_rate" and remote_value is None:
                    continue
                # 未計測の進捗率はExcelへ書き戻さない。
                if field_name == "progress_rate" and is_unmeasured_progress_rate(local):
                    continue
                local_value = getattr(local, field_name)
                if local_value == remote_value:
                    continue
                baseline_value = (
                    getattr(affected_record.previous, field_name)
                    if affected_record.previous is not None
                    else None
                )
                derived_fields[field_name] = as_written_conflict_field(
                    IquavisTaskConflictField(
                        field=field_name,
                        baseline=_sync_json_value(baseline_value),
                        qolony=_sync_json_value(local_value),
                        excel=_sync_json_value(remote_value),
                    )
                )
            if not derived_fields:
                continue

            derived_updates.append(
                IquavisEditorQolonyUpdate(
                    iquavis_id=affected_record.iquavis_id,
                    properties=local,
                    field_names=tuple(derived_fields),
                )
            )
            existing = conflicts.get(affected_task_id)
            if existing is not None and existing.sync_state == IQUAVIS_CONFLICT_STATE:
                conflicts[affected_task_id] = replace(
                    existing,
                    recorded_at=now,
                    pending_fields={**existing.pending_fields, **derived_fields},
                )
            elif existing is not None and existing.sync_state == IQUAVIS_PENDING_SAVE_STATE:
                conflicts[affected_task_id] = replace(
                    existing,
                    recorded_at=now,
                    fields={**existing.fields, **derived_fields},
                )
            else:
                conflicts[affected_task_id] = IquavisTaskConflict(
                    task_id=affected_task_id,
                    iquavis_id=affected_record.iquavis_id,
                        message="QOLONYの再計算値をExcelへ書き出しました。ExcelからiQUAVISへ保存してください。",
                    recorded_at=now,
                    fields=derived_fields,
                    pending_fields={},
                    workbook_fingerprint=current_fingerprint,
                    sync_state=IQUAVIS_PENDING_SAVE_STATE,
                )

        if derived_updates:
            write_result = workbook_adapter.write_qolony_updates(resolved_path, derived_updates)
            requested_ids = {update.iquavis_id for update in derived_updates}
            missing_ids = sorted(requested_ids - set(write_result.updated_ids))
            if missing_ids:
                raise UserInputError(f"QOLONY再計算値のExcel書き出し対象が見つかりません: {', '.join(missing_ids)}")

        final_fingerprint = self._workbook_fingerprint(resolved_path)
        conflicts = {
            existing_task_id: replace(existing_conflict, workbook_fingerprint=final_fingerprint)
            for existing_task_id, existing_conflict in conflicts.items()
        }
        self.repository.set_state(IQUAVIS_TASK_CONFLICT_STATE_KEY, serialize_conflict_state(conflicts))
        pending = conflicts.get(task_id)
        if pending is not None and pending.sync_state == IQUAVIS_PENDING_SAVE_STATE:
            sync_state = IQUAVIS_PENDING_SAVE_STATE
            recorded_at = pending.recorded_at.isoformat()
        else:
            sync_state = "Linked"
            recorded_at = None
        return {
            "status": "ok",
            "task_id": task_id,
            "direction": direction,
            "sync_state": sync_state,
            "recorded_at": recorded_at,
        }

    def get_latest_event(self) -> SyncEvent | None:
        raw = self.repository.get_state(LATEST_SYNC_EVENT_STATE_KEY)
        if not raw:
            return None
        try:
            payload = json.loads(raw)
            return SyncEvent(
                message=str(payload.get("message", "")).strip(),
                is_error=bool(payload.get("is_error", False)),
                recorded_at=datetime.fromisoformat(str(payload["recorded_at"])),
            )
        except Exception:
            return None

    def _execute_outbound_job(self, *, task: Task, job_type: str, payload: dict[str, Any]) -> None:
        if job_type == "Schedule":
            self.client.put_task_schedule(
                task.IQuavisTaskId or "",
                start_date=date.fromisoformat(str(payload["start_date"])),
                end_date=date.fromisoformat(str(payload["end_date"])),
            )
            return

        if job_type == "Progress":
            progress_rate = payload.get("progress_rate")
            try:
                self.client.put_task_progress(
                    task.IQuavisTaskId or "",
                    status=str(payload["status"]),
                    progress_rate=int(progress_rate) if progress_rate is not None else None,
                )
            except IQuavisApiError as exc:
                if exc.status_code == 400 and progress_rate is not None:
                # ProgressType が手動進捗ではないタスクでは Status のみを送れば通る可能性がある
                    self.client.put_task_progress(
                        task.IQuavisTaskId or "",
                        status=str(payload["status"]),
                        progress_rate=None,
                    )
                    return
                raise
            return

        raise ValueError(f"unsupported iquavis job type: {job_type}")

    def _apply_remote_task_to_local(self, local_task_id: str, remote: IQuavisRemoteTask) -> bool:
        task = self.repository.get_task(local_task_id)
        if task is None:
            return False

        changed = False
        if task.StartDate != remote.start_date or task.EndDate != remote.end_date:
            all_tasks = self.repository.list_tasks(task.ProjectId)
            self.tms_service._validate_task_schedule(task, all_tasks, remote.start_date, remote.end_date)
            self.repository.update_task_schedule(task.Id, remote.start_date, remote.end_date)
            changed = True

        if remote.status == "Completion" and task.Status != "Completion":
            self.repository.update_task_status(task.Id, "Completion", 100.0)
            changed = True
        elif remote.status == "Abort" and task.Status != "Abort":
            self.repository.update_task_status(task.Id, "Abort", 0.0)
            changed = True

        if changed:
            self.tms_service.recompute_all_task_progress()
        return changed

    def _apply_imported_progress_state(self, *, task_id: str, status: str | None, progress_rate: int | None) -> None:
        if status == "Completion":
            self.repository.update_task_status(task_id, "Completion", 100.0)
            return
        if status == "NotStart":
            self.repository.update_task_status(task_id, "NotStart", 0.0)
            return
        if status == "DuringStart":
            self.repository.update_task_status(task_id, "DuringStart", float(progress_rate or 0))
            return
        if status == "Abort":
            self.repository.update_task_status(task_id, "Abort", 0.0)

    def _resolve_local_assignee_id(self, remote: IQuavisRemoteTask, *, default_assignee_id: str) -> str:
        return self._resolve_local_assignee_id_fields(
            first_assign_iquavis_user_id=remote.first_assign_iquavis_user_id,
            first_assign_login_id=remote.first_assign_login_id,
            first_assign_name=remote.first_assign_name,
            creator_iquavis_user_id=remote.creator_iquavis_user_id,
            creator_login_id=remote.creator_login_id,
            creator_name=remote.creator_name,
            default_assignee_id=default_assignee_id,
        )

    def _resolve_local_assignee_id_fields(
        self,
        *,
        first_assign_iquavis_user_id: str | None,
        first_assign_login_id: str | None,
        first_assign_name: str | None,
        creator_iquavis_user_id: str | None = None,
        creator_login_id: str | None = None,
        creator_name: str | None = None,
        default_assignee_id: str,
    ) -> str:
        assignee_user_id = self._resolve_local_user_id_by_fields(
            iquavis_user_id=first_assign_iquavis_user_id,
            login_id=first_assign_login_id,
            name=first_assign_name,
        )
        if assignee_user_id is not None:
            return assignee_user_id

        if not (first_assign_iquavis_user_id or first_assign_login_id or first_assign_name):
            creator_user_id = self._resolve_local_user_id_by_fields(
                iquavis_user_id=creator_iquavis_user_id,
                login_id=creator_login_id,
                name=creator_name,
            )
            if creator_user_id is not None:
                return creator_user_id

        return default_assignee_id

    def _resolve_local_user_id_by_fields(
        self,
        *,
        iquavis_user_id: str | None,
        login_id: str | None,
        name: str | None,
    ) -> str | None:
        if iquavis_user_id:
            user = self.repository.get_user_by_iquavis_user_id(iquavis_user_id)
            if user is not None:
                return user.Id

        if login_id:
            user = self.repository.get_user_by_login_id(login_id)
            if user is not None:
                return user.Id

            user = self.repository.get_user_by_name(login_id)
            if user is not None:
                return user.Id

        if name:
            user = self.repository.get_user_by_name(name)
            if user is not None:
                return user.Id

            # iQUAVIS側のユーザー名が「姓　名」形式でも、QOLONY側で姓のみでも拾えるようにする
            short_name = str(name).replace("\u3000", " ").split(" ")[0].strip()
            if short_name:
                user = self.repository.get_user_by_name(short_name)
                if user is not None:
                    return user.Id
        return None

    def _resolve_local_parent_for_import(
        self,
        *,
        parent_override_local_task_id: str | None,
        remote_parent_task_id: str | None,
        local_id_by_remote_id: dict[str, str],
    ) -> str | None:
        if parent_override_local_task_id:
            local_parent = self.repository.get_task(parent_override_local_task_id)
            if local_parent is not None:
                return local_parent.Id

        if remote_parent_task_id:
            local_parent_id = local_id_by_remote_id.get(remote_parent_task_id)
            if local_parent_id:
                return local_parent_id
            existing = self.repository.get_task_by_iquavis_task_id(remote_parent_task_id)
            if existing is not None:
                return existing.Id
        return None

    def _order_remote_ids(self, remote_by_id: dict[str, IQuavisRemoteTask]) -> list[str]:
        ordered: list[str] = []
        remaining = dict(remote_by_id)

        while remaining:
            progressed = False
            for task_id, remote in list(remaining.items()):
                parent_id = remote.parent_task_id
                if not parent_id or parent_id not in remaining:
                    ordered.append(task_id)
                    del remaining[task_id]
                    progressed = True
            if progressed:
                continue

            # 親循環があっても全体が止まらないよう、残りをそのまま吐き出す。
            ordered.extend(sorted(remaining.keys()))
            break

        return ordered

    def _progress_rate_as_int(self, value: float) -> int:
        return int(max(0, min(100, round_half_up(value))))

    def _is_remote_project_allowed(self, project_id: str | None) -> bool:
        allowed = self.settings.iquavis.allowed_project_ids
        if not allowed:
            return True
        return bool(project_id and project_id in allowed)

    def _with_link_target_targets(self, snapshot: IquavisEditorSnapshot) -> IquavisEditorSnapshot:
        targets = self._read_link_target_targets()
        if not targets:
            return snapshot
        merged = self._merge_synced_targets([*snapshot.synced_targets, *targets])
        target_by_id = {target.iquavis_id: target for target in merged}
        return IquavisEditorSnapshot(
            synced_targets=merged,
            task_domains=snapshot.task_domains,
            tasks=snapshot.tasks,
            members=snapshot.members,
            task_sync_records=[
                replace(
                    record,
                    force_sync=(
                        target_by_id[record.iquavis_id].force_sync
                        if record.iquavis_id in target_by_id and target_by_id[record.iquavis_id].force_sync
                        else record.force_sync
                    ),
                )
                for record in snapshot.task_sync_records
            ],
        )

    def _build_authoritative_record_review_key(self, row_index: int) -> str:
        return f"row:{row_index}"

    def _normalize_review_item_keys(self, ignored_review_item_keys: list[str] | None) -> set[str]:
        normalized: set[str] = set()
        for key in ignored_review_item_keys or []:
            value = str(key).strip()
            if value:
                normalized.add(value)
        return normalized

    def _filter_authoritative_task_sync_records(
        self,
        snapshot: IquavisEditorSnapshot,
        *,
        ignored_iquavis_ids: list[str],
        ignored_review_item_keys: set[str],
    ) -> IquavisEditorSnapshot:
        if not ignored_iquavis_ids and not ignored_review_item_keys:
            return snapshot
        ignored_iquavis_id_set = {_effective_iquavis_id(ignored_id) for ignored_id in ignored_iquavis_ids}
        filtered: list[IquavisTaskSyncRecord] = []
        for row_index, record in enumerate(snapshot.task_sync_records):
            review_key = self._build_authoritative_record_review_key(row_index)
            effective_id = _effective_iquavis_id(record.iquavis_id)
            if review_key in ignored_review_item_keys:
                continue
            if f"id:{effective_id}" in ignored_review_item_keys and effective_id:
                continue
            if effective_id and effective_id in ignored_iquavis_id_set:
                continue
            filtered.append(record)
        return replace(snapshot, task_sync_records=filtered)

    def _without_excluded_targets(
        self,
        snapshot: IquavisEditorSnapshot,
        ignored_iquavis_ids: list[str] | None = None,
    ) -> IquavisEditorSnapshot:
        excluded_task_ids = self.tms_service.get_iquavis_sync_excluded_task_ids()
        normalized_ignored_ids = {
            ignored_id
            for ignored_id in (_effective_iquavis_id(ignored_id) for ignored_id in (ignored_iquavis_ids or []))
            if ignored_id
        }
        if not excluded_task_ids and not normalized_ignored_ids:
            return snapshot

        excluded_iquavis_ids: set[str] = {
            task.IQuavisTaskId
            for task in (self.repository.get_task(task_id) for task_id in sorted(excluded_task_ids))
            if task is not None and _effective_iquavis_id(task.IQuavisTaskId)
        }
        excluded_iquavis_ids.update(normalized_ignored_ids)
        if not excluded_iquavis_ids:
            return snapshot

        changed = True
        while changed:
            changed = False
            for domain in snapshot.task_domains:
                if domain.iquavis_task_domain_id in excluded_iquavis_ids:
                    continue
                if domain.parent_task_domain_id and domain.parent_task_domain_id in excluded_iquavis_ids:
                    excluded_iquavis_ids.add(domain.iquavis_task_domain_id)
                    changed = True
            for task in snapshot.tasks:
                if task.iquavis_task_id in excluded_iquavis_ids:
                    continue
                if task.task_domain_id and task.task_domain_id in excluded_iquavis_ids:
                    excluded_iquavis_ids.add(task.iquavis_task_id)
                    changed = True
                    continue
                if task.parent_task_id and task.parent_task_id in excluded_iquavis_ids:
                    excluded_iquavis_ids.add(task.iquavis_task_id)
                    changed = True

        return IquavisEditorSnapshot(
            synced_targets=[target for target in snapshot.synced_targets if target.iquavis_id not in excluded_iquavis_ids],
            task_domains=[
                domain
                for domain in snapshot.task_domains
                if domain.iquavis_task_domain_id not in excluded_iquavis_ids
            ],
            tasks=[
                task
                for task in snapshot.tasks
                if task.iquavis_task_id not in excluded_iquavis_ids
            ],
            members=snapshot.members,
            task_sync_records=[
                record for record in snapshot.task_sync_records if record.iquavis_id not in excluded_iquavis_ids
            ],
        )

    def _read_link_target_targets(self) -> list[SyncedIquavisTarget]:
        workbook_path = self.settings.iquavis.link_targets_workbook_path
        if workbook_path is None:
            return []
        path = Path(workbook_path)
        if not path.is_absolute():
            path = (Path(__file__).resolve().parents[2] / path).resolve()
        return read_sync_targets(path)

    def _clear_link_target_force_sync_requests(self, iquavis_ids: list[str]) -> list[str]:
        workbook_path = self.settings.iquavis.link_targets_workbook_path
        if workbook_path is None:
            return []
        path = Path(workbook_path)
        if not path.is_absolute():
            path = (Path(__file__).resolve().parents[2] / path).resolve()
        return clear_force_sync_requests(path, iquavis_ids)

    def _workbook_fingerprint(self, workbook_path: Path) -> str:
        digest = hashlib.sha256()
        with workbook_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _merge_synced_targets(self, targets: list[SyncedIquavisTarget]) -> list[SyncedIquavisTarget]:
        merged: dict[str, SyncedIquavisTarget] = {}
        for target in targets:
            iquavis_id = _effective_iquavis_id(target.iquavis_id)
            if not iquavis_id:
                continue
            existing = merged.get(iquavis_id)
            if existing is None:
                merged[iquavis_id] = replace(target, iquavis_id=iquavis_id)
                continue
            merged[iquavis_id] = SyncedIquavisTarget(
                iquavis_id=iquavis_id,
                kind=existing.kind if existing.kind != "unknown" else target.kind,
                force_sync=existing.force_sync or target.force_sync,
                qolony_task_id=existing.qolony_task_id or target.qolony_task_id,
                qolony_parent_task_id=existing.qolony_parent_task_id or target.qolony_parent_task_id,
            )
        return list(merged.values())

    def _retry_delay_seconds(self, attempt: int) -> int:
        base = max(1, int(self.settings.iquavis.retry_base_seconds))
        return min(base * (2 ** max(0, attempt - 1)), 3600)

    def _job_type_label(self, job_type: str) -> str:
        if job_type == "Schedule":
            return "日稁E"
        if job_type == "Progress":
            return "進捁E"
        return job_type

    def _publish_event(self, message: str, is_error: bool) -> None:
        payload = {
            "message": message,
            "is_error": bool(is_error),
            "recorded_at": datetime.now().isoformat(),
        }
        self.repository.set_state(
            LATEST_SYNC_EVENT_STATE_KEY,
            json.dumps(payload, ensure_ascii=False),
        )
