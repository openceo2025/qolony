from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
import hashlib
from pathlib import Path
from typing import Any, Callable

from app.iquavis.editor_models import (
    IquavisEditorQolonyUpdate,
    IquavisEditorSnapshot,
    IquavisTaskSyncProperties,
    IquavisTaskSyncRecord,
)
from app.iquavis.editor_workbook import IquavisEditorWorkbookAdapter
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
from app.tms_core.db.models import Task
from app.tms_core.db.repository import TmsRepository
from app.tms_core.domain.rounding import round_half_up
from app.tms_core.services.core import TmsService, UserInputError


@dataclass(frozen=True)
class IquavisEditorConflictSyncResult:
    local_updates_written: list[str] = field(default_factory=list)
    remote_updates_applied: list[str] = field(default_factory=list)
    forced_sync_cleared: list[str] = field(default_factory=list)
    conflict_task_ids: list[str] = field(default_factory=list)
    pending_iquavis_save_task_ids: list[str] = field(default_factory=list)
    skipped_missing_local_ids: list[str] = field(default_factory=list)
    # 非稼働日のためiQUAVISへ送れなかったタスク。運用者が日付を直す必要がある。
    non_working_date_task_ids: list[str] = field(default_factory=list)
    # 前回の保存が通らず、今回書き直して再送対象に戻したタスク。
    retried_task_ids: list[str] = field(default_factory=list)


SUPPORTED_SYNC_FIELDS: tuple[str, ...] = (
    "name",
    "start_date",
    "end_date",
    "actual_minutes",
    "status",
    "progress_rate",
    "memo",
)


def _effective_iquavis_id(value: object) -> str:
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

# 進捗率は実績・見積・状態・子タスクからQOLONYが算出するため、採用方向を選ばせない。
QOLONY_DERIVED_SYNC_FIELDS: tuple[str, ...] = ("progress_rate",)
PARENT_DERIVED_SYNC_FIELDS: tuple[str, ...] = ("actual_minutes", "status")
QOLONY_TASK_STATUSES: frozenset[str] = frozenset({"NotStart", "DuringStart", "Completion", "Abort"})
IQUAVIS_DERIVED_CONFLICT_MESSAGE = (
    "QOLONYの親子集計により、iQUAVISの進捗・実績・状態をそのまま表現できません。"
    "これらのQOLONY値はExcelへ自動送信しません。同期対象外、親子構造の調整、"
    "またはQOLONY値の採用を選択してください。"
)


def is_iquavis_derived_conflict(conflict: IquavisTaskConflict | None) -> bool:
    return conflict is not None and conflict.message == IQUAVIS_DERIVED_CONFLICT_MESSAGE


def is_unmeasured_progress_rate(properties: IquavisTaskSyncProperties) -> bool:
    """QOLONYの進捗率が「実測した0」ではなく「まだ何も分かっていない」かを判定する。

    QOLONYは進捗率を完了状態・実績工数・見積工数・子タスクから算出する。
    実績が1分も無いタスクの進捗率0は、計測結果ではなく未知を意味する。
    これをExcelへ書き出してiQUAVISへ保存すると、iQUAVIS側の進捗が0で消える。
    実運用でiQUAVISの50%等に対しQOLONYの0%が106件書き出された事例があるため、送らない。
    """
    if properties.progress_rate is None:
        return True
    if int(properties.progress_rate) != 0:
        return False
    # 完了は状態そのものが100%を意味するので計測結果として扱う。
    # 中止は別で、QOLONYの「Abort→0」は仕様上の割り当てにすぎない。
    # iQUAVIS側は中止時点まで進んだ率（例:50%）を保持しているので、0で消してはいけない。
    if properties.status == "Completion":
        return False
    return int(properties.actual_minutes) <= 0


def non_working_date_fields(
    properties: IquavisTaskSyncProperties,
    is_business_day: Callable[[date], bool],
) -> tuple[str, ...]:
    """iQUAVISが受け付けない非稼働日の日付項目を返す。

    iQUAVISはプロジェクトカレンダーの非稼働日を予定日に設定できず、保存時に
    CED0129「予定終了日が非稼働日に設定されています」で拒否する。
    土日祝の日付をExcelへ黄色で書いても「サーバーへ保存」で必ず失敗し、
    しかも失敗が1件でも混ざるとVBAが全体を中断するため、最初から送らない。
    """
    return tuple(
        field_name
        for field_name in ("start_date", "end_date")
        if not is_business_day(getattr(properties, field_name))
    )


def _without_non_working_dates(
    fields: dict[str, IquavisTaskConflictField],
    local: IquavisTaskSyncProperties,
    is_business_day: Callable[[date], bool] | None,
) -> tuple[dict[str, IquavisTaskConflictField], tuple[str, ...]]:
    """非稼働日の日付をExcelへの書き出し対象から外す。"""
    if is_business_day is None:
        return fields, ()
    blocked = tuple(
        field_name
        for field_name in non_working_date_fields(local, is_business_day)
        if field_name in fields
    )
    if not blocked:
        return fields, ()
    return (
        {name: values for name, values in fields.items() if name not in blocked},
        blocked,
    )


def _without_unmeasured_progress(
    fields: dict[str, IquavisTaskConflictField],
    local: IquavisTaskSyncProperties,
) -> dict[str, IquavisTaskConflictField]:
    """未計測の進捗率をExcelへの書き出し対象から外す。"""
    if "progress_rate" not in fields or not is_unmeasured_progress_rate(local):
        return fields
    return {
        field_name: values for field_name, values in fields.items() if field_name != "progress_rate"
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    return value


def as_written_conflict_field(values: IquavisTaskConflictField) -> IquavisTaskConflictField:
    """Excelへ書き出した後の表示値へ正規化する。"""
    return replace(values, excel=values.qolony)


class IquavisEditorConflictSyncService:
    def __init__(self, *, repository: TmsRepository, tms_service: TmsService):
        self.repository = repository
        self.tms_service = tms_service

    def sync_snapshot(
        self,
        snapshot: IquavisEditorSnapshot,
        *,
        workbook_path: Path | None = None,
        adapter: IquavisEditorWorkbookAdapter | None = None,
        now: datetime | None = None,
        workbook_fingerprint: str | None = None,
    ) -> IquavisEditorConflictSyncResult:
        now = now or datetime.now()
        self.tms_service.recompute_all_task_progress()
        project = self.tms_service.get_project()
        project_tasks = self.repository.list_tasks(project.Id)
        task_by_id = {task.Id: task for task in project_tasks}
        task_by_iquavis_id = {
            _effective_iquavis_id(task.IQuavisTaskId): task
            for task in project_tasks
            if _effective_iquavis_id(task.IQuavisTaskId)
        }
        local_by_task_id, parent_task_ids = self._local_properties_context(tasks=project_tasks)
        stored = parse_conflict_state(self.repository.get_state(IQUAVIS_TASK_CONFLICT_STATE_KEY))
        records = [record for record in snapshot.task_sync_records if _effective_iquavis_id(record.iquavis_id)]
        record_ids = {_effective_iquavis_id(record.iquavis_id) for record in records}
        # 通常Conflictは毎回三者比較から再構築し、除外・削除・ブックから消えた古い状態を残さない。
        conflicts = {
            task_id: conflict
            for task_id, conflict in stored.items()
            if (
                (
                    conflict.sync_state == IQUAVIS_PENDING_SAVE_STATE
                    or conflict.sync_state == IQUAVIS_CONFLICT_HOLD_STATE
                    or is_iquavis_derived_conflict(conflict)
                )
                and conflict.iquavis_id in record_ids
                and task_id in task_by_id
            )
        }
        local_updates: list[IquavisEditorQolonyUpdate] = []
        remote_updates: list[tuple[str, IquavisTaskSyncProperties, tuple[str, ...]]] = []
        clear_force_ids: list[str] = []
        conflict_task_ids: list[str] = []
        pending_task_ids: list[str] = []
        skipped_missing_local_ids: list[str] = []
        non_working_date_task_ids: list[str] = []
        retry_task_ids: list[str] = []
        processed_task_ids: set[str] = set()
        is_business_day = getattr(self.tms_service, "_is_business_day", None)

        for record in records:
            record_iquavis_id = _effective_iquavis_id(record.iquavis_id)
            task = task_by_iquavis_id.get(record_iquavis_id)
            if task is None:
                skipped_missing_local_ids.append(record_iquavis_id)
                continue
            processed_task_ids.add(task.Id)

            local = local_by_task_id.get(task.Id, self._local_properties(task))
            previous = record.previous
            remote = record.iquavis_current
            existing = stored.get(task.Id)
            protected_field_names = (
                set(existing.fields)
                if is_iquavis_derived_conflict(existing)
                else set()
            )
            waiting_pending_fields: dict[str, IquavisTaskConflictField] = {}
            retried_field_names: set[str] = set()
            if existing is not None and existing.sync_state == IQUAVIS_PENDING_SAVE_STATE:
                pending_status = self._pending_save_status(existing, local=local, previous=previous, remote=remote)
                if pending_status == "waiting":
                    # 未計測の進捗率はもうExcelへ送らない。過去に書き出した分も保存待ちから外す。
                    retained = _without_unmeasured_progress(existing.fields, local)
                    if retained:
                        waiting_pending_fields = retained
                        conflicts[task.Id] = (
                            existing if len(retained) == len(existing.fields) else replace(existing, fields=retained)
                        )
                    else:
                        conflicts.pop(task.Id, None)
                if pending_status == "confirmed":
                    conflicts.pop(task.Id, None)
            carried_pending_fields = _without_unmeasured_progress(
                self._waiting_conflict_pending_fields(
                    existing,
                    local=local,
                    previous=previous,
                    remote=remote,
                ),
                local,
            )

            bidirectional_fields = self._bidirectional_sync_fields(task.Id, parent_task_ids)
            comparison_fields = tuple(
                field_name for field_name in bidirectional_fields if field_name not in waiting_pending_fields
            )
            local_fields, remote_fields, true_conflicts = self._compare_fields(
                local=local,
                previous=previous,
                remote=remote,
                field_names=comparison_fields,
            )

            # 既存baselineから継続するAbort等は表現差として保持し、新規に変化した場合だけ選択を求める。
            unsupported_status: IquavisTaskConflictField | None = None
            if (
                "status" in bidirectional_fields
                and remote.status not in QOLONY_TASK_STATUSES
                and local.status != remote.status
            ):
                baseline_status = previous.status if previous is not None else None
                unsupported_values = IquavisTaskConflictField(
                    field="status",
                    baseline=_json_value(baseline_status),
                    qolony=_json_value(local.status),
                    excel=_json_value(remote.status),
                )
                local_fields.pop("status", None)
                remote_fields.pop("status", None)
                true_conflicts.pop("status", None)
                if (
                    record.force_sync == "qolony_to_iquavis"
                    or record.force_sync == "iquavis_to_qolony"
                    or previous is None
                    or previous.status != remote.status
                    or local.status != "NotStart"
                ):
                    unsupported_status = unsupported_values

            if record.force_sync == "qolony_to_iquavis":
                local_fields.update(remote_fields)
                local_fields.update(true_conflicts)
                remote_fields = {}
                true_conflicts = {}
                if unsupported_status is not None:
                    local_fields["status"] = unsupported_status
                clear_force_ids.append(record_iquavis_id)
            elif record.force_sync == "iquavis_to_qolony":
                remote_fields.update(local_fields)
                remote_fields.update(true_conflicts)
                local_fields = {}
                true_conflicts = {}
                if unsupported_status is not None:
                    true_conflicts["status"] = unsupported_status
                elif not protected_field_names:
                    clear_force_ids.append(record_iquavis_id)
            elif unsupported_status is not None:
                true_conflicts["status"] = unsupported_status

            if remote_fields:
                remote_updates.append((task.Id, remote, tuple(remote_fields)))
            # 非稼働日の日付はiQUAVISが保存時に拒否する。Excelへ黄色で書かず、運用者へ知らせる。
            local_fields, blocked_date_fields = _without_non_working_dates(
                local_fields, local, is_business_day
            )
            if blocked_date_fields:
                non_working_date_task_ids.append(task.Id)

            # 保存待ちのまま残っている項目は、毎回書き直して黄色へ戻す。
            # Excelの「サーバーへ保存」が失敗するとセルは赤になり、VBAは黄色しか送らないため、
            # 書き直さないと二度と再送されず永久に止まる（iQUAVIS側の進捗が更新されない事故の原因）。
            retry_fields = _without_non_working_dates(
                _without_unmeasured_progress(waiting_pending_fields, local),
                local,
                is_business_day,
            )[0]
            for field_name, values in retry_fields.items():
                local_fields.setdefault(field_name, values)
                if field_name not in retried_field_names:
                    retried_field_names.add(field_name)
                    retry_task_ids.append(task.Id)

            if local_fields:
                local_updates.append(
                    IquavisEditorQolonyUpdate(
                        iquavis_id=record_iquavis_id,
                        properties=local,
                        field_names=tuple(local_fields),
                    )
                )

            if true_conflicts:
                merged_pending_fields = {
                    **waiting_pending_fields,
                    **carried_pending_fields,
                    **{field_name: as_written_conflict_field(values) for field_name, values in local_fields.items()},
                }
                new_conflict = IquavisTaskConflict(
                    task_id=task.Id,
                    iquavis_id=record_iquavis_id,
                    message="QOLONY側とExcel側で同じ項目が異なる値に変更されています。採用する側を選択してください。",
                    recorded_at=now,
                    fields=true_conflicts,
                    pending_fields=merged_pending_fields,
                    workbook_fingerprint=workbook_fingerprint,
                    sync_state=IQUAVIS_CONFLICT_STATE,
                )
                if existing is not None and self._same_conflict(existing, new_conflict):
                    new_conflict = replace(new_conflict, recorded_at=existing.recorded_at)
                conflicts[task.Id] = new_conflict
                conflict_task_ids.append(task.Id)
            elif local_fields or carried_pending_fields or waiting_pending_fields:
                pending_fields = {
                    **waiting_pending_fields,
                    **carried_pending_fields,
                    **{field_name: as_written_conflict_field(values) for field_name, values in local_fields.items()},
                }
                conflicts[task.Id] = IquavisTaskConflict(
                    task_id=task.Id,
                    iquavis_id=record_iquavis_id,
                    message=(
                        existing.message
                        if existing is not None and waiting_pending_fields and not local_fields
                        else "QOLONYの変更をExcelへ書き出しました。ExcelからiQUAVISへ保存してください。"
                    ),
                    recorded_at=(
                        existing.recorded_at
                        if existing is not None and waiting_pending_fields and not local_fields
                        else now
                    ),
                    fields=pending_fields,
                    pending_fields={},
                    workbook_fingerprint=workbook_fingerprint,
                    sync_state=IQUAVIS_PENDING_SAVE_STATE,
                )
                pending_task_ids.append(task.Id)
            elif not protected_field_names:
                conflicts.pop(task.Id, None)

        applied_ids: list[str] = []
        for task_id, properties, field_names in remote_updates:
            self._apply_remote_fields(task_id, properties, field_names)
            self.repository.mark_task_iquavis_last_pulled(task_id, now)
            applied_ids.append(task_id)

        if applied_ids:
            self.tms_service.recompute_all_task_progress()

        # remote反映後の再計算値を使い、親への波及も同じ同期でExcelへ戻す。
        final_local_by_task_id, final_parent_task_ids = self._local_properties_context()
        update_by_iquavis_id = {
            _effective_iquavis_id(update.iquavis_id): update for update in local_updates
        }
        for update in tuple(update_by_iquavis_id.values()):
            update_iquavis_id = _effective_iquavis_id(update.iquavis_id)
            task = task_by_iquavis_id.get(update_iquavis_id) if update_iquavis_id else None
            if task is None or task.Id not in final_local_by_task_id:
                continue
            update_by_iquavis_id[update_iquavis_id] = replace(
                update,
                properties=final_local_by_task_id[task.Id],
            )

        for record in records:
            record_iquavis_id = _effective_iquavis_id(record.iquavis_id)
            task = task_by_iquavis_id.get(record_iquavis_id)
            if task is None:
                continue
            local = final_local_by_task_id.get(task.Id, self._local_properties(task))
            derived_fields: dict[str, IquavisTaskConflictField] = {}
            protected_derived_fields: dict[str, IquavisTaskConflictField] = {}
            stored_conflict = stored.get(task.Id)
            protected_field_names = (
                set(stored_conflict.fields)
                if is_iquavis_derived_conflict(stored_conflict)
                and record.force_sync != "qolony_to_iquavis"
                else set()
            )
            for field_name in self._qolony_owned_sync_fields(task.Id, final_parent_task_ids):
                remote_value = getattr(record.iquavis_current, field_name)
                if field_name == "progress_rate" and remote_value is None:
                    continue
                # 実績が無く完了・中止でもないタスクの進捗率0は未知でしかない。iQUAVISの値を消さない。
                if field_name == "progress_rate" and is_unmeasured_progress_rate(local):
                    continue
                local_value = getattr(local, field_name)
                if local_value == remote_value:
                    continue
                baseline_value = getattr(record.previous, field_name) if record.previous is not None else None
                values = IquavisTaskConflictField(
                    field=field_name,
                    baseline=_json_value(baseline_value),
                    qolony=_json_value(local_value),
                    excel=_json_value(remote_value),
                )
                if field_name in protected_field_names:
                    protected_derived_fields[field_name] = values
                else:
                    derived_fields[field_name] = as_written_conflict_field(values)

            derived_fields, blocked_derived_dates = _without_non_working_dates(
                derived_fields, local, is_business_day
            )
            if blocked_derived_dates:
                non_working_date_task_ids.append(task.Id)
            if derived_fields:
                existing_update = update_by_iquavis_id.get(record_iquavis_id)
                field_names = tuple(
                    dict.fromkeys(
                        [
                            *(existing_update.field_names if existing_update is not None else ()),
                            *derived_fields,
                        ]
                    )
                )
                update_by_iquavis_id[record_iquavis_id] = IquavisEditorQolonyUpdate(
                    iquavis_id=record_iquavis_id,
                    properties=local,
                    field_names=field_names,
                )

                conflict = conflicts.get(task.Id)
                if conflict is not None and conflict.sync_state == IQUAVIS_CONFLICT_STATE:
                    conflicts[task.Id] = replace(
                        conflict,
                        pending_fields={**conflict.pending_fields, **derived_fields},
                    )
                elif conflict is not None and conflict.sync_state == IQUAVIS_PENDING_SAVE_STATE:
                    conflicts[task.Id] = replace(conflict, fields={**conflict.fields, **derived_fields})
                else:
                    conflicts[task.Id] = IquavisTaskConflict(
                        task_id=task.Id,
                        iquavis_id=record_iquavis_id,
                        message="QOLONYの再計算値をExcelへ書き出しました。ExcelからiQUAVISへ保存してください。",
                        recorded_at=now,
                        fields=derived_fields,
                        pending_fields={},
                        workbook_fingerprint=workbook_fingerprint,
                        sync_state=IQUAVIS_PENDING_SAVE_STATE,
                    )
                pending_task_ids.append(task.Id)

            if protected_derived_fields:
                current = conflicts.get(task.Id)
                if current is not None and current.sync_state == IQUAVIS_PENDING_SAVE_STATE:
                    other_fields: dict[str, IquavisTaskConflictField] = {}
                    pending_fields = current.fields
                else:
                    other_fields = {
                        field_name: values
                        for field_name, values in (current.fields.items() if current is not None else ())
                        if field_name not in protected_field_names
                    }
                    pending_fields = current.pending_fields if current is not None else {}
                next_conflict = IquavisTaskConflict(
                    task_id=task.Id,
                    iquavis_id=record_iquavis_id,
                    message=IQUAVIS_DERIVED_CONFLICT_MESSAGE,
                    recorded_at=now,
                    fields={**other_fields, **protected_derived_fields},
                    pending_fields=pending_fields,
                    workbook_fingerprint=workbook_fingerprint,
                    sync_state=IQUAVIS_CONFLICT_STATE,
                )
                if stored_conflict is not None and self._same_conflict(stored_conflict, next_conflict):
                    next_conflict = replace(next_conflict, recorded_at=stored_conflict.recorded_at)
                conflicts[task.Id] = next_conflict
                conflict_task_ids.append(task.Id)
            elif protected_field_names:
                current = conflicts.get(task.Id)
                if current is not None and is_iquavis_derived_conflict(current):
                    if current.pending_fields:
                        conflicts[task.Id] = replace(
                            current,
                            message="QOLONYの変更をExcelへ書き出しました。ExcelからiQUAVISへ保存してください。",
                            fields=current.pending_fields,
                            pending_fields={},
                            sync_state=IQUAVIS_PENDING_SAVE_STATE,
                        )
                        pending_task_ids.append(task.Id)
                    else:
                        conflicts.pop(task.Id, None)

        local_updates = list(update_by_iquavis_id.values())
        written_ids: list[str] = []
        if local_updates:
            if adapter is None or workbook_path is None:
                raise UserInputError("QOLONY側変更を書き戻すためのiquavis_editor.xlsmアダプタが未設定です")
            write_result = adapter.write_qolony_updates(workbook_path, local_updates)
            written_ids = list(dict.fromkeys(write_result.updated_ids))
            requested_ids = {update.iquavis_id for update in local_updates}
            missing_ids = sorted(requested_ids - set(written_ids))
            if missing_ids:
                raise UserInputError(f"QOLONY変更のExcel書き出し対象が見つかりません: {', '.join(missing_ids)}")

        cleared_ids: list[str] = []
        if clear_force_ids:
            if adapter is None or workbook_path is None:
                raise UserInputError("強制同期カラムをクリアするためのiquavis_editor.xlsmアダプタが未設定です")
            clear_result = adapter.clear_force_sync_requests(workbook_path, list(dict.fromkeys(clear_force_ids)))
            cleared_ids = list(dict.fromkeys([*clear_result.cleared_force_sync_ids, *clear_force_ids]))

        final_fingerprint = self._workbook_fingerprint(workbook_path) or workbook_fingerprint
        if final_fingerprint:
            conflicts = {
                task_id: replace(conflict, workbook_fingerprint=final_fingerprint)
                if conflict.fields and task_id in processed_task_ids
                else conflict
                for task_id, conflict in conflicts.items()
            }

        self.repository.set_state(IQUAVIS_TASK_CONFLICT_STATE_KEY, serialize_conflict_state(conflicts))
        return IquavisEditorConflictSyncResult(
            local_updates_written=written_ids,
            remote_updates_applied=applied_ids,
            forced_sync_cleared=cleared_ids,
            conflict_task_ids=conflict_task_ids,
            pending_iquavis_save_task_ids=list(dict.fromkeys(pending_task_ids)),
            skipped_missing_local_ids=skipped_missing_local_ids,
            non_working_date_task_ids=list(dict.fromkeys(non_working_date_task_ids)),
            retried_task_ids=list(dict.fromkeys(retry_task_ids)),
        )

    def _local_properties(
        self,
        task: Task,
        *,
        actual_minutes: int | None = None,
        progress_rate: float | None = None,
    ) -> IquavisTaskSyncProperties:
        return IquavisTaskSyncProperties(
            name=task.Name.strip(),
            start_date=task.StartDate,
            end_date=task.EndDate,
            assigned_minutes=int(task.AssignedManHour),
            actual_minutes=int(task.ActualManHour if actual_minutes is None else actual_minutes),
            status=task.Status.strip(),
            progress_rate=int(
                max(
                    0,
                    min(
                        100,
                        round_half_up(task.ProgressRate if progress_rate is None else progress_rate),
                    ),
                )
            ),
            memo=(task.Memo or "").strip(),
        )

    def local_sync_properties(
        self,
        *,
        tasks: list[Task] | None = None,
    ) -> tuple[dict[str, IquavisTaskSyncProperties], set[str]]:
        """QOLONY現在値（親タスクは子からの集計込み）と、親タスクIDの集合を返す。"""
        return self._local_properties_context(tasks=tasks)

    def _local_properties_context(
        self,
        *,
        tasks: list[Task] | None = None,
    ) -> tuple[dict[str, IquavisTaskSyncProperties], set[str]]:
        if tasks is None:
            project = self.tms_service.get_project()
            tasks = self.repository.list_tasks(project.Id)
        aggregates = self.tms_service._build_task_aggregates(tasks)
        parent_task_ids = {task.ParentTaskId for task in tasks if task.ParentTaskId is not None}
        properties: dict[str, IquavisTaskSyncProperties] = {}
        for task in tasks:
            aggregate = aggregates.get(task.Id)
            properties[task.Id] = self._local_properties(
                task,
                actual_minutes=(aggregate.ActualTotal if aggregate is not None else task.ActualManHour),
                progress_rate=(aggregate.ProgressRate if aggregate is not None else task.ProgressRate),
            )
        return properties, parent_task_ids

    def _qolony_owned_sync_fields(self, task_id: str, parent_task_ids: set[str]) -> tuple[str, ...]:
        owned = set(QOLONY_DERIVED_SYNC_FIELDS)
        if task_id in parent_task_ids:
            owned.update(PARENT_DERIVED_SYNC_FIELDS)
        return tuple(field_name for field_name in SUPPORTED_SYNC_FIELDS if field_name in owned)

    def _bidirectional_sync_fields(self, task_id: str, parent_task_ids: set[str]) -> tuple[str, ...]:
        owned = set(self._qolony_owned_sync_fields(task_id, parent_task_ids))
        return tuple(field_name for field_name in SUPPORTED_SYNC_FIELDS if field_name not in owned)

    def _compare_fields(
        self,
        *,
        local: IquavisTaskSyncProperties,
        previous: IquavisTaskSyncProperties | None,
        remote: IquavisTaskSyncProperties,
        field_names: tuple[str, ...] | None = None,
    ) -> tuple[
        dict[str, IquavisTaskConflictField],
        dict[str, IquavisTaskConflictField],
        dict[str, IquavisTaskConflictField],
    ]:
        local_only: dict[str, IquavisTaskConflictField] = {}
        remote_only: dict[str, IquavisTaskConflictField] = {}
        conflicts: dict[str, IquavisTaskConflictField] = {}
        for field_name in SUPPORTED_SYNC_FIELDS if field_names is None else field_names:
            local_value = getattr(local, field_name)
            remote_value = getattr(remote, field_name)
            # 列がないブックでは進捗率を未対応として扱い、0との疑似差分を作らない。
            if field_name == "progress_rate" and remote_value is None:
                continue
            baseline_value = getattr(previous, field_name) if previous is not None else None
            values = IquavisTaskConflictField(
                field=field_name,
                baseline=_json_value(baseline_value),
                qolony=_json_value(local_value),
                excel=_json_value(remote_value),
            )
            if previous is None:
                if local_value != remote_value:
                    conflicts[field_name] = values
                continue
            if field_name == "progress_rate" and baseline_value is None:
                if local_value != remote_value:
                    conflicts[field_name] = values
                continue
            if local_value == remote_value:
                continue
            local_changed = local_value != baseline_value
            remote_changed = remote_value != baseline_value
            if local_changed and not remote_changed:
                local_only[field_name] = values
            elif remote_changed and not local_changed:
                remote_only[field_name] = values
            elif local_changed and remote_changed:
                conflicts[field_name] = values
        return local_only, remote_only, conflicts

    def _pending_save_status(
        self,
        conflict: IquavisTaskConflict,
        *,
        local: IquavisTaskSyncProperties,
        previous: IquavisTaskSyncProperties | None,
        remote: IquavisTaskSyncProperties,
    ) -> str:
        if not conflict.fields:
            return "changed"
        waiting = previous is None
        for field_name, values in conflict.fields.items():
            if field_name not in SUPPORTED_SYNC_FIELDS:
                return "changed"
            expected = values.qolony
            if _json_value(getattr(local, field_name)) != expected:
                return "changed"
            if _json_value(getattr(remote, field_name)) != expected:
                return "changed"
            if previous is None or _json_value(getattr(previous, field_name)) != expected:
                waiting = True
        return "waiting" if waiting else "confirmed"

    def _waiting_conflict_pending_fields(
        self,
        conflict: IquavisTaskConflict | None,
        *,
        local: IquavisTaskSyncProperties,
        previous: IquavisTaskSyncProperties | None,
        remote: IquavisTaskSyncProperties,
    ) -> dict[str, IquavisTaskConflictField]:
        if conflict is None or conflict.sync_state != IQUAVIS_CONFLICT_STATE:
            return {}
        waiting: dict[str, IquavisTaskConflictField] = {}
        for field_name, values in conflict.pending_fields.items():
            if field_name not in SUPPORTED_SYNC_FIELDS:
                continue
            expected = values.qolony
            if _json_value(getattr(local, field_name)) != expected:
                continue
            if _json_value(getattr(remote, field_name)) != expected:
                continue
            if previous is None or _json_value(getattr(previous, field_name)) != expected:
                waiting[field_name] = values
        return waiting

    def _same_conflict(self, previous: IquavisTaskConflict, current: IquavisTaskConflict) -> bool:
        return (
            previous.sync_state == IQUAVIS_CONFLICT_STATE
            and previous.iquavis_id == current.iquavis_id
            and previous.fields == current.fields
            and previous.pending_fields == current.pending_fields
            and previous.workbook_fingerprint == current.workbook_fingerprint
        )

    def _workbook_fingerprint(self, workbook_path: Path | None) -> str | None:
        if workbook_path is None or not workbook_path.exists():
            return None
        digest = hashlib.sha256()
        with workbook_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _apply_remote_fields(
        self,
        task_id: str,
        properties: IquavisTaskSyncProperties,
        field_names: tuple[str, ...],
    ) -> None:
        task = self.repository.get_task(task_id)
        if task is None:
            return
        selected = set(field_names)
        if "name" in selected and task.Name != properties.name:
            self.repository.update_task_name(task_id, properties.name)
        start_date = properties.start_date if "start_date" in selected else task.StartDate
        end_date = properties.end_date if "end_date" in selected else task.EndDate
        if task.StartDate != start_date or task.EndDate != end_date:
            self.repository.update_task_schedule(task_id, start_date, end_date)
        if "actual_minutes" in selected and task.ActualManHour != properties.actual_minutes:
            self.repository.update_task_actual_manhour(task_id, max(0, int(properties.actual_minutes)))
        if "memo" in selected and (task.Memo or "").strip() != properties.memo:
            self.repository.update_task_memo(task_id, properties.memo)
        if "status" in selected or "progress_rate" in selected:
            status = properties.status if "status" in selected else task.Status
            progress_rate = (
                float(properties.progress_rate or 0)
                if "progress_rate" in selected
                else float(task.ProgressRate)
            )
            if status == "Completion":
                progress_rate = 100.0
            elif status == "NotStart":
                progress_rate = 0.0
            self.repository.update_task_status(task_id, status, progress_rate)
