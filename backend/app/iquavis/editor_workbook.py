from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol

from openpyxl import load_workbook
from openpyxl.utils.cell import get_column_letter

from app.iquavis.editor_models import (
    IquavisAssigneeRecord,
    IquavisEditorQolonyUpdate,
    IquavisEditorSnapshot,
    IquavisEditorSyncWriteResult,
    IquavisForceSyncDirection,
    IquavisMemberRecord,
    IquavisSyncTargetKind,
    IquavisTaskDomainRecord,
    IquavisTaskRecord,
    IquavisTaskSyncProperties,
    IquavisTaskSyncRecord,
    SyncedIquavisTarget,
)
from app.iquavis.ooxml_patch import OoxmlCellPatch, patch_ooxml_cells


class IquavisEditorWorkbookError(RuntimeError):
    pass


class IquavisEditorWorkbookAdapter(Protocol):
    def load_snapshot(self, workbook_path: Path) -> IquavisEditorSnapshot:
        raise NotImplementedError

    def write_qolony_updates(
        self,
        workbook_path: Path,
        updates: list[IquavisEditorQolonyUpdate],
    ) -> IquavisEditorSyncWriteResult:
        raise NotImplementedError

    def restore_iquavis_values(
        self,
        workbook_path: Path,
        updates: list[IquavisEditorQolonyUpdate],
    ) -> IquavisEditorSyncWriteResult:
        raise NotImplementedError

    def clear_force_sync_requests(
        self,
        workbook_path: Path,
        iquavis_ids: list[str],
    ) -> IquavisEditorSyncWriteResult:
        raise NotImplementedError


@dataclass(frozen=True)
class _WorksheetHeader:
    sheet_name: str
    header_row: int
    columns: dict[str, int]
    is_visible: bool


_ALIASES: dict[str, tuple[str, ...]] = {
    "id": (
        "id",
        "taskid",
        "タスクid",
        "iquavisid",
        "iquavistaskid",
        "同期id",
        "同期対象id",
    ),
    "name": ("name", "名称", "タスク名", "taskname"),
    "kind": ("kind", "種別", "区分", "type"),
    "type": ("type", "タスク種別"),
    "target": ("読み取り対象", "同期対象", "取り込み対象", "qolony同期対象"),
    "force_sync": ("強制同期", "forcesync", "force_sync", "強制同期方向"),
    "project_id": ("projectid", "project_id", "プロジェクトid"),
    "task_domain_id": ("taskdomainid", "task_domain_id", "タスクドメインid"),
    "parent_task_id": ("parenttaskid", "parent_task_id", "親タスクid"),
    "start_date": ("startdate", "start_date", "開始日"),
    "end_date": ("enddate", "end_date", "期限", "終了日"),
    "assigned": ("assignedmanhour", "assigned_minutes", "assignedminutes", "見積mh", "見積工数"),
    "actual": (
        "progress.actualmanhour",
        "actualmanhour",
        "actual_minutes",
        "actualminutes",
        "実績mh",
        "実績工数",
    ),
    "status": ("progress.status", "progressstatus", "status", "状態"),
    "progress_rate": ("progress.progressrate", "progressrate", "progress_rate", "進捗率"),
    # VBAのタスク実績保存契約に合わせ、Task.NoteよりProgress.Memoを優先する。
    "memo": ("progress.memo", "memo", "progress.note", "note", "メモ", "備考"),
    "assigns": ("assigns", "assign", "担当", "担当者"),
    "login_id": ("loginid", "login_id", "ログインid"),
    "iquavis_user_id": ("iquavisuserid", "iquavis_user_id", "userid", "user_id", "ユーザーid"),
    "creator": ("creator", "createdby", "createuser", "createduser", "作成者"),
    "creator_user_id": (
        "createuserid",
        "create_user_id",
        "creatoruserid",
        "creator_user_id",
        "createduserid",
        "created_user_id",
        "createdbyuserid",
        "created_by_user_id",
        "作成者id",
        "作成者ユーザーid",
    ),
    "creator_login_id": ("creatorloginid", "creator_login_id", "createuserloginid", "作成者ログインid"),
    "creator_name": ("creatorname", "creator_name", "createusername", "作成者名"),
}
_ALIASES["parent_task_domain_id"] = ("parenttaskdomainid", "parent_task_domain_id")
_ALIASES["full_name"] = ("fullname", "full_name")


def _normalize(value: Any) -> str:
    text = "" if value is None else str(value)
    return (
        text.strip()
        .replace("\u3000", "")
        .replace(" ", "")
        .replace("_", "")
        .replace("-", "")
        .lower()
    )


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _as_effective_iquavis_id(value: Any) -> str:
    text = _as_text(value)
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


def _truthy_mark(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = _as_text(value).lower()
    return text in {"●", "○", "o", "yes", "true", "1", "y", "対象", "同期", "sync"}


def _header_map(row: tuple[Any, ...]) -> dict[str, int]:
    result: dict[str, int] = {}
    for index, value in enumerate(row, start=1):
        normalized = _normalize(value)
        if normalized:
            result[normalized] = index
    return result


def _column(header: _WorksheetHeader, key: str) -> int | None:
    aliases = _ALIASES[key]
    for alias in aliases:
        column = header.columns.get(_normalize(alias))
        if column is not None:
            return column
    return None


def _is_vba_task_snapshot_sheet(sheet_name: str) -> bool:
    return sheet_name.strip().lower().startswith("__tasks_")


def _is_original_task_sheet(sheet_name: str) -> bool:
    normalized = _normalize(sheet_name)
    return (
        _is_vba_task_snapshot_sheet(sheet_name)
        or normalized in {"original", "_original"}
        or "original" in normalized
    )


def _cell_value(row: tuple[Any, ...], column: int | None) -> Any:
    if column is None:
        return None
    index = column - 1
    if index < 0 or index >= len(row):
        return None
    return row[index]


def _parse_date(value: Any, *, default: date) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _as_text(value)
    if not text:
        return default
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return default


def _parse_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    text = _as_text(value).replace(",", "")
    if not text:
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def _parse_force_sync(value: Any) -> IquavisForceSyncDirection | None:
    normalized = _normalize(value)
    if not normalized:
        return None
    if normalized in {"qolonytoiquavis", "qolony>iquavis", "qolony→iquavis", "qolonyからiquavis", "qolony優先"}:
        return "qolony_to_iquavis"
    if normalized in {"iquavistoqolony", "iquavis>qolony", "iquavis→qolony", "iquavisからqolony", "iquavis優先"}:
        return "iquavis_to_qolony"
    return None


def _parse_kind(value: Any) -> IquavisSyncTargetKind:
    normalized = _normalize(value)
    if normalized in {"task", "タスク"}:
        return "task"
    if normalized in {"taskdomain", "task_domain", "タスクドメイン", "domain"}:
        return "task_domain"
    return "unknown"


def _kind_from_task_type(value: str) -> IquavisSyncTargetKind:
    normalized = _normalize(value)
    if normalized in {"taskdomain", "domain"}:
        return "task_domain"
    return "task"


def _parse_assigns(value: Any) -> IquavisAssigneeRecord | None:
    text = _as_text(value)
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return IquavisAssigneeRecord(name=text)
    if not isinstance(payload, list) or not payload:
        return None
    first = payload[0]
    if not isinstance(first, dict):
        return None
    return IquavisAssigneeRecord(
        iquavis_user_id=_as_text(first.get("Id")) or _as_text(first.get("UserId")) or None,
        login_id=_as_text(first.get("LoginId")) or None,
        name=_as_text(first.get("Name")) or None,
    )


def _parse_user_reference(value: Any) -> IquavisAssigneeRecord | None:
    text = _as_text(value)
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return IquavisAssigneeRecord(iquavis_user_id=text, login_id=text, name=text)
    if isinstance(payload, list):
        payload = payload[0] if payload else None
    if not isinstance(payload, dict):
        return IquavisAssigneeRecord(iquavis_user_id=text, login_id=text, name=text)
    return IquavisAssigneeRecord(
        iquavis_user_id=_as_text(payload.get("Id")) or _as_text(payload.get("UserId")) or None,
        login_id=_as_text(payload.get("LoginId")) or None,
        name=_as_text(payload.get("Name")) or None,
    )


def _parse_user_reference_from_columns(row: tuple[Any, ...], header: _WorksheetHeader) -> IquavisAssigneeRecord | None:
    base = _parse_user_reference(_cell_value(row, _column(header, "creator")))
    iquavis_user_id = _as_text(_cell_value(row, _column(header, "creator_user_id"))) or None
    login_id = _as_text(_cell_value(row, _column(header, "creator_login_id"))) or None
    name = _as_text(_cell_value(row, _column(header, "creator_name"))) or None
    if base is None and not (iquavis_user_id or login_id or name):
        return None
    return IquavisAssigneeRecord(
        iquavis_user_id=iquavis_user_id or (base.iquavis_user_id if base else None),
        login_id=login_id or (base.login_id if base else None),
        name=name or (base.name if base else None),
    )


class OpenpyxlIquavisEditorWorkbookAdapter:
    def load_snapshot(self, workbook_path: Path) -> IquavisEditorSnapshot:
        if not workbook_path.exists():
            raise FileNotFoundError(f"iquavis_editor.xlsm not found: {workbook_path}")

        workbook = load_workbook(workbook_path, read_only=True, data_only=True, keep_vba=False)
        try:
            headers = self._find_headers(workbook)
            target_headers = [header for header in headers if self._is_target_sheet(header)]
            task_headers = [header for header in headers if self._is_task_sheet(header)]
            current_task_headers = [header for header in task_headers if self._is_current_task_sheet(header)]
            task_domain_headers = [header for header in headers if self._is_task_domain_sheet(header)]
            member_headers = [header for header in headers if self._is_member_sheet(header)]

            task_domains = self._read_task_domains(workbook, task_domain_headers)
            tasks = self._read_tasks(workbook, current_task_headers)
            synced_targets = self._auto_complete_synced_targets(
                explicit_targets=self._read_synced_targets(workbook, target_headers, current_task_headers),
                task_domains=task_domains,
                tasks=tasks,
            )
            members = self._read_members(workbook, member_headers)
            sync_records = self._build_sync_records(workbook, task_headers, synced_targets)
            return IquavisEditorSnapshot(
                synced_targets=synced_targets,
                task_domains=task_domains,
                tasks=tasks,
                members=members,
                task_sync_records=sync_records,
            )
        finally:
            workbook.close()

    def write_qolony_updates(
        self,
        workbook_path: Path,
        updates: list[IquavisEditorQolonyUpdate],
    ) -> IquavisEditorSyncWriteResult:
        if not workbook_path.exists():
            raise FileNotFoundError(f"iquavis_editor.xlsm not found: {workbook_path}")
        if not updates:
            return IquavisEditorSyncWriteResult()

        workbook = load_workbook(workbook_path, read_only=True, data_only=True, keep_vba=False)
        patches: list[OoxmlCellPatch] = []
        updated_ids: list[str] = []
        try:
            update_by_id = {update.iquavis_id: update for update in updates}
            expected_ids = {
                iquavis_id
                for iquavis_id, update in update_by_id.items()
                if self._qolony_update_values(update)
            }
            for header in self._find_headers(workbook):
                if not self._is_current_task_sheet(header):
                    continue
                worksheet = workbook[header.sheet_name]
                id_col = _column(header, "id")
                if id_col is None:
                    continue
                for row_number, row in enumerate(
                    worksheet.iter_rows(min_row=header.header_row + 1, values_only=True),
                    start=header.header_row + 1,
                ):
                    iquavis_id = _as_text(_cell_value(row, id_col))
                    update = update_by_id.get(iquavis_id)
                    if update is None:
                        continue
                    row_patches: list[OoxmlCellPatch] = []
                    requested_values = self._qolony_update_values(update)
                    for key, value in requested_values:
                        column = _column(header, key)
                        if column is None:
                            row_patches = []
                            break
                        row_patches.append(
                            OoxmlCellPatch(
                                sheet_name=header.sheet_name,
                                coordinate=f"{get_column_letter(column)}{row_number}",
                                value=value,
                                yellow_fill=True,
                            )
                        )
                    if row_patches and len(row_patches) == len(requested_values):
                        patches.extend(row_patches)
                        updated_ids.append(iquavis_id)
        finally:
            workbook.close()
        if expected_ids - set(updated_ids):
            return IquavisEditorSyncWriteResult()
        patch_ooxml_cells(workbook_path, patches)
        return IquavisEditorSyncWriteResult(updated_ids=list(dict.fromkeys(updated_ids)))

    def restore_iquavis_values(
        self,
        workbook_path: Path,
        updates: list[IquavisEditorQolonyUpdate],
    ) -> IquavisEditorSyncWriteResult:
        """visibleセルをhidden取得成功値へ戻し、変更黄色を解除する。"""
        if not workbook_path.exists():
            raise FileNotFoundError(f"iquavis_editor.xlsm not found: {workbook_path}")
        if not updates:
            return IquavisEditorSyncWriteResult()

        workbook = load_workbook(workbook_path, read_only=True, data_only=True, keep_vba=False)
        patches: list[OoxmlCellPatch] = []
        updated_ids: list[str] = []
        try:
            update_by_id = {update.iquavis_id: update for update in updates}
            expected_ids = {
                iquavis_id
                for iquavis_id, update in update_by_id.items()
                if self._restore_update_values(update)
            }
            for header in self._find_headers(workbook):
                if not self._is_current_task_sheet(header):
                    continue
                worksheet = workbook[header.sheet_name]
                id_col = _column(header, "id")
                if id_col is None:
                    continue
                for row_number, row in enumerate(
                    worksheet.iter_rows(min_row=header.header_row + 1, values_only=True),
                    start=header.header_row + 1,
                ):
                    iquavis_id = _as_text(_cell_value(row, id_col))
                    update = update_by_id.get(iquavis_id)
                    if update is None:
                        continue
                    requested_values = self._restore_update_values(update)
                    row_patches: list[OoxmlCellPatch] = []
                    for key, value in requested_values:
                        column = _column(header, key)
                        if column is None:
                            row_patches = []
                            break
                        row_patches.append(
                            OoxmlCellPatch(
                                sheet_name=header.sheet_name,
                                coordinate=f"{get_column_letter(column)}{row_number}",
                                value=value,
                                clear_yellow_fill=True,
                            )
                        )
                    if row_patches and len(row_patches) == len(requested_values):
                        patches.extend(row_patches)
                        updated_ids.append(iquavis_id)
        finally:
            workbook.close()
        if expected_ids - set(updated_ids):
            return IquavisEditorSyncWriteResult()
        patch_ooxml_cells(workbook_path, patches)
        return IquavisEditorSyncWriteResult(updated_ids=list(dict.fromkeys(updated_ids)))

    def clear_force_sync_requests(
        self,
        workbook_path: Path,
        iquavis_ids: list[str],
    ) -> IquavisEditorSyncWriteResult:
        if not workbook_path.exists():
            raise FileNotFoundError(f"iquavis_editor.xlsm not found: {workbook_path}")
        target_ids = set(iquavis_ids)
        if not target_ids:
            return IquavisEditorSyncWriteResult()

        workbook = load_workbook(workbook_path, read_only=True, data_only=True, keep_vba=False)
        patches: list[OoxmlCellPatch] = []
        cleared_ids: list[str] = []
        try:
            for header in self._find_headers(workbook):
                if self._is_task_sheet(header) and not self._is_current_task_sheet(header):
                    continue
                force_col = _column(header, "force_sync")
                id_col = _column(header, "id")
                if force_col is None or id_col is None:
                    continue
                worksheet = workbook[header.sheet_name]
                for row_number, row in enumerate(
                    worksheet.iter_rows(min_row=header.header_row + 1, values_only=True),
                    start=header.header_row + 1,
                ):
                    iquavis_id = _as_text(_cell_value(row, id_col))
                    if iquavis_id not in target_ids:
                        continue
                    if not _as_text(_cell_value(row, force_col)):
                        continue
                    patches.append(
                        OoxmlCellPatch(
                            sheet_name=header.sheet_name,
                            coordinate=f"{get_column_letter(force_col)}{row_number}",
                            value=None,
                        )
                    )
                    cleared_ids.append(iquavis_id)
        finally:
            workbook.close()
        patch_ooxml_cells(workbook_path, patches)
        return IquavisEditorSyncWriteResult(cleared_force_sync_ids=list(dict.fromkeys(cleared_ids)))

    def _find_headers(self, workbook: Any) -> list[_WorksheetHeader]:
        headers: list[_WorksheetHeader] = []
        for worksheet in workbook.worksheets:
            max_scan = min(20, worksheet.max_row or 0)
            for row_number, values in enumerate(
                worksheet.iter_rows(min_row=1, max_row=max_scan, values_only=True),
                start=1,
            ):
                columns = _header_map(values)
                candidate = _WorksheetHeader(
                    worksheet.title,
                    row_number,
                    columns,
                    worksheet.sheet_state == "visible",
                )
                if _column(candidate, "id") is not None and (
                    _column(candidate, "name") is not None
                    or _column(candidate, "target") is not None
                    or _column(candidate, "force_sync") is not None
                    or _column(candidate, "login_id") is not None
                ):
                    headers.append(candidate)
                    break
        return headers

    def _is_target_sheet(self, header: _WorksheetHeader) -> bool:
        if _column(header, "id") is None:
            return False
        if _column(header, "force_sync") is not None:
            return True
        title = _normalize(header.sheet_name)
        return "同期" in title or "sync" in title

    def _is_task_sheet(self, header: _WorksheetHeader) -> bool:
        return (
            _column(header, "id") is not None
            and _column(header, "name") is not None
            and _column(header, "start_date") is not None
            and _column(header, "end_date") is not None
        )

    def _is_current_task_sheet(self, header: _WorksheetHeader) -> bool:
        return self._is_task_sheet(header) and header.is_visible and not _is_original_task_sheet(header.sheet_name)

    def _is_task_domain_sheet(self, header: _WorksheetHeader) -> bool:
        if not header.is_visible:
            return False
        if self._is_task_sheet(header):
            return False
        if (
            _column(header, "id") is None
            or _column(header, "name") is None
            or _column(header, "project_id") is None
        ):
            return False
        if _column(header, "parent_task_domain_id") is not None:
            return True
        normalized_title = _normalize(header.sheet_name)
        # ルートだけのタスクドメイン出力では ParentTaskDomainId 列が無いことがある。
        return "taskdomain" in normalized_title or "タスクドメイン" in header.sheet_name

    def _is_member_sheet(self, header: _WorksheetHeader) -> bool:
        return (
            not self._is_task_sheet(header)
            and _column(header, "name") is not None
            and (_column(header, "login_id") is not None or _column(header, "iquavis_user_id") is not None)
        )

    def _read_synced_targets(
        self,
        workbook: Any,
        target_headers: list[_WorksheetHeader],
        task_headers: list[_WorksheetHeader],
    ) -> list[SyncedIquavisTarget]:
        targets: list[SyncedIquavisTarget] = []

        for header in target_headers:
            worksheet = workbook[header.sheet_name]
            id_col = _column(header, "id")
            kind_col = _column(header, "kind")
            force_col = _column(header, "force_sync")
            if id_col is None:
                continue
            for row in worksheet.iter_rows(min_row=header.header_row + 1, values_only=True):
                iquavis_id = _as_effective_iquavis_id(_cell_value(row, id_col))
                if not iquavis_id:
                    continue
                targets.append(
                    SyncedIquavisTarget(
                        iquavis_id=iquavis_id,
                        kind=_parse_kind(_cell_value(row, kind_col)),
                        force_sync=_parse_force_sync(_cell_value(row, force_col)),
                    )
                )

        for header in task_headers:
            target_col = _column(header, "target")
            if target_col is None:
                continue
            worksheet = workbook[header.sheet_name]
            id_col = _column(header, "id")
            type_col = _column(header, "type")
            force_col = _column(header, "force_sync")
            if id_col is None:
                continue
            for row in worksheet.iter_rows(min_row=header.header_row + 1, values_only=True):
                if not _truthy_mark(_cell_value(row, target_col)):
                    continue
                iquavis_id = _as_effective_iquavis_id(_cell_value(row, id_col))
                if not iquavis_id:
                    continue
                targets.append(
                    SyncedIquavisTarget(
                        iquavis_id=iquavis_id,
                        kind=_kind_from_task_type(_as_text(_cell_value(row, type_col))),
                        force_sync=_parse_force_sync(_cell_value(row, force_col)),
                    )
                )

        merged: dict[str, SyncedIquavisTarget] = {}
        for target in targets:
            existing = merged.get(target.iquavis_id)
            if existing is None:
                merged[target.iquavis_id] = target
                continue
            merged[target.iquavis_id] = SyncedIquavisTarget(
                iquavis_id=target.iquavis_id,
                kind=existing.kind if existing.kind != "unknown" else target.kind,
                force_sync=existing.force_sync or target.force_sync,
                qolony_task_id=existing.qolony_task_id or target.qolony_task_id,
                qolony_parent_task_id=existing.qolony_parent_task_id or target.qolony_parent_task_id,
            )
        return list(merged.values())

    def _read_tasks(self, workbook: Any, task_headers: list[_WorksheetHeader]) -> list[IquavisTaskRecord]:
        tasks: dict[str, IquavisTaskRecord] = {}
        duplicate_tasks: list[IquavisTaskRecord] = []
        today = date.today()
        for header in task_headers:
            if not self._is_current_task_sheet(header):
                continue
            worksheet = workbook[header.sheet_name]
            id_col = _column(header, "id")
            if id_col is None:
                continue
            for row in worksheet.iter_rows(min_row=header.header_row + 1, values_only=True):
                iquavis_id = _as_effective_iquavis_id(_cell_value(row, id_col))
                name = _as_text(_cell_value(row, _column(header, "name")))
                if not iquavis_id or not name:
                    continue
                task_type = _as_text(_cell_value(row, _column(header, "type"))) or "Task"
                task_record = IquavisTaskRecord(
                        iquavis_task_id=iquavis_id,
                        name=name,
                        project_id=_as_text(_cell_value(row, _column(header, "project_id"))) or None,
                        task_domain_id=_as_text(_cell_value(row, _column(header, "task_domain_id"))) or None,
                        parent_task_id=_as_text(_cell_value(row, _column(header, "parent_task_id"))) or None,
                        start_date=_parse_date(_cell_value(row, _column(header, "start_date")), default=today),
                        end_date=_parse_date(_cell_value(row, _column(header, "end_date")), default=today),
                        assigned_minutes=_parse_int(_cell_value(row, _column(header, "assigned"))),
                        note=_as_text(_cell_value(row, _column(header, "memo"))),
                        task_type=task_type,
                        status=_as_text(_cell_value(row, _column(header, "status"))) or None,
                        progress_rate=_parse_int(_cell_value(row, _column(header, "progress_rate"))) or None,
                        assignee=_parse_assigns(_cell_value(row, _column(header, "assigns"))),
                        creator=_parse_user_reference_from_columns(row, header),
                    )
                if iquavis_id in tasks:
                    # preview側で重複IDを安全にblockできるよう、readerで捨てない。
                    duplicate_tasks.append(task_record)
                else:
                    tasks[iquavis_id] = task_record
        return [*tasks.values(), *duplicate_tasks]

    def _auto_complete_synced_targets(
        self,
        *,
        explicit_targets: list[SyncedIquavisTarget],
        task_domains: list[IquavisTaskDomainRecord],
        tasks: list[IquavisTaskRecord],
    ) -> list[SyncedIquavisTarget]:
        # iQUAVIS editor の「全タスク」出力に含まれるIDは、明示マークがなくてもQOLONY同期対象にする。
        merged: dict[str, SyncedIquavisTarget] = {
            target.iquavis_id: target
            for target in explicit_targets
            if target.iquavis_id
        }
        for domain in task_domains:
            domain_id = _as_effective_iquavis_id(domain.iquavis_task_domain_id)
            if domain_id and domain_id not in merged:
                merged[domain_id] = SyncedIquavisTarget(iquavis_id=domain_id, kind="task_domain")
        for task in tasks:
            task_id = _as_effective_iquavis_id(task.iquavis_task_id)
            if task_id and task_id not in merged:
                merged[task_id] = SyncedIquavisTarget(iquavis_id=task_id, kind="task")
        return list(merged.values())

    def _read_task_domains(
        self,
        workbook: Any,
        task_domain_headers: list[_WorksheetHeader],
    ) -> list[IquavisTaskDomainRecord]:
        task_domains: dict[str, IquavisTaskDomainRecord] = {}
        duplicate_domains: list[IquavisTaskDomainRecord] = []
        today = date.today()
        for header in task_domain_headers:
            worksheet = workbook[header.sheet_name]
            id_col = _column(header, "id")
            if id_col is None:
                continue
            for row in worksheet.iter_rows(min_row=header.header_row + 1, values_only=True):
                domain_id = _as_effective_iquavis_id(_cell_value(row, id_col))
                if not domain_id:
                    continue
                name = _as_text(_cell_value(row, _column(header, "name")))
                full_name = _as_text(_cell_value(row, _column(header, "full_name")))
                if not name and full_name:
                    name = full_name.split(".")[-1].strip()
                if not name:
                    continue
                domain_record = IquavisTaskDomainRecord(
                        iquavis_task_domain_id=domain_id,
                        name=name,
                        project_id=_as_text(_cell_value(row, _column(header, "project_id"))) or None,
                        parent_task_domain_id=_as_text(_cell_value(row, _column(header, "parent_task_domain_id"))) or None,
                        start_date=today,
                        end_date=today,
                        note=full_name,
                        assignee=None,
                        creator=_parse_user_reference_from_columns(row, header),
                    )
                if domain_id in task_domains:
                    # preview側で重複IDを安全にblockできるよう、readerで捨てない。
                    duplicate_domains.append(domain_record)
                else:
                    task_domains[domain_id] = domain_record
        return [*task_domains.values(), *duplicate_domains]

    def _read_members(self, workbook: Any, member_headers: list[_WorksheetHeader]) -> list[IquavisMemberRecord]:
        members: dict[str, IquavisMemberRecord] = {}
        for header in member_headers:
            worksheet = workbook[header.sheet_name]
            name_col = _column(header, "name")
            if name_col is None:
                continue
            for row in worksheet.iter_rows(min_row=header.header_row + 1, values_only=True):
                name = _as_text(_cell_value(row, name_col))
                if not name:
                    continue
                member = IquavisMemberRecord(
                    iquavis_user_id=_as_text(_cell_value(row, _column(header, "iquavis_user_id"))) or None,
                    login_id=_as_text(_cell_value(row, _column(header, "login_id"))) or None,
                    name=name,
                )
                members[member.iquavis_user_id or member.login_id or member.name] = member
        return list(members.values())

    def _build_sync_records(
        self,
        workbook: Any,
        task_headers: list[_WorksheetHeader],
        targets: list[SyncedIquavisTarget],
    ) -> list[IquavisTaskSyncRecord]:
        current = self._task_properties_by_id(workbook, task_headers, original=False)
        previous = self._task_properties_by_id(workbook, task_headers, original=True)
        target_by_id = {target.iquavis_id: target for target in targets}
        records: list[IquavisTaskSyncRecord] = []
        for iquavis_id, target in target_by_id.items():
            current_props = current.get(iquavis_id)
            if current_props is None:
                continue
            records.append(
                IquavisTaskSyncRecord(
                    iquavis_id=iquavis_id,
                    kind=target.kind,
                    previous=previous.get(iquavis_id),
                    iquavis_current=current_props,
                    force_sync=target.force_sync,
                )
            )
        return records

    def _task_properties_by_id(
        self,
        workbook: Any,
        task_headers: list[_WorksheetHeader],
        *,
        original: bool,
    ) -> dict[str, IquavisTaskSyncProperties]:
        result: dict[str, IquavisTaskSyncProperties] = {}
        today = date.today()
        for header in task_headers:
            if original:
                if not _is_original_task_sheet(header.sheet_name):
                    continue
            elif not self._is_current_task_sheet(header):
                continue
            worksheet = workbook[header.sheet_name]
            id_col = _column(header, "id")
            if id_col is None:
                continue
            for row_number, row in enumerate(
                worksheet.iter_rows(min_row=header.header_row + 1, values_only=True),
                start=header.header_row + 1,
            ):
                iquavis_id = _as_effective_iquavis_id(_cell_value(row, id_col))
                name = _as_text(_cell_value(row, _column(header, "name")))
                if not iquavis_id or not name:
                    continue
                if original and iquavis_id in result:
                    raise IquavisEditorWorkbookError(
                        "非表示の取得成功値に同じiQUAVIS IDが複数あります: "
                        f"{iquavis_id} ({header.sheet_name}!{row_number})"
                    )
                if not original and iquavis_id in result:
                    # visible側の重複は _read_tasks が保持し、初期整合previewでblockする。
                    continue
                result[iquavis_id] = IquavisTaskSyncProperties(
                    name=name,
                    start_date=_parse_date(_cell_value(row, _column(header, "start_date")), default=today),
                    end_date=_parse_date(_cell_value(row, _column(header, "end_date")), default=today),
                    assigned_minutes=_parse_int(_cell_value(row, _column(header, "assigned"))),
                    actual_minutes=_parse_int(_cell_value(row, _column(header, "actual"))),
                    status=_as_text(_cell_value(row, _column(header, "status"))) or "NotStart",
                    progress_rate=(
                        _parse_int(_cell_value(row, _column(header, "progress_rate")))
                        if _column(header, "progress_rate") is not None
                        else None
                    ),
                    memo=_as_text(_cell_value(row, _column(header, "memo"))),
                )
        return result

    def _qolony_update_values(
        self,
        update: IquavisEditorQolonyUpdate,
    ) -> list[tuple[str, Any]]:
        values: dict[str, tuple[str, Any]] = {
            "name": ("name", update.properties.name),
            "start_date": ("start_date", update.properties.start_date),
            "end_date": ("end_date", update.properties.end_date),
            "actual_minutes": ("actual", update.properties.actual_minutes),
            "status": ("status", update.properties.status),
            "progress_rate": ("progress_rate", update.properties.progress_rate),
            "memo": ("memo", update.properties.memo),
        }
        return [
            values[field_name]
            for field_name in update.field_names
            if field_name in values
        ]

    def _restore_update_values(
        self,
        update: IquavisEditorQolonyUpdate,
    ) -> list[tuple[str, Any]]:
        """初期整合の復元だけはread-only見積列も取得成功値へ戻す。"""
        values: dict[str, tuple[str, Any]] = {
            "name": ("name", update.properties.name),
            "start_date": ("start_date", update.properties.start_date),
            "end_date": ("end_date", update.properties.end_date),
            "assigned_minutes": ("assigned", update.properties.assigned_minutes),
            "actual_minutes": ("actual", update.properties.actual_minutes),
            "status": ("status", update.properties.status),
            "progress_rate": ("progress_rate", update.properties.progress_rate),
            "memo": ("memo", update.properties.memo),
        }
        return [
            values[field_name]
            for field_name in update.field_names
            if field_name in values
        ]
