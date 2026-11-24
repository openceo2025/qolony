from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.worksheet import Worksheet

from app.iquavis.editor_models import IquavisForceSyncDirection, SyncedIquavisTarget
from app.tms_core.db.models import User


MEMBER_SHEET_NAME = "QOLONY_members"
TASK_LINK_SHEET_NAME = "QOLONY_linked_tasks"
TASK_DOMAIN_LINK_SHEET_NAME = "QOLONY_linked_task_domains"
MEMBER_HEADERS = [
    "use",
    "qolony_user_id",
    "nickname",
    "full_name",
    "login_id",
    "iquavis_user_id",
    "is_top_manager",
    "is_active",
    "note",
]


@dataclass(frozen=True)
class LinkTargetMemberRow:
    qolony_user_id: str | None
    nickname: str
    full_name: str
    login_id: str | None
    iquavis_user_id: str | None
    is_top_manager: bool
    is_active: bool
    note: str


def _force_sync(value: Any) -> IquavisForceSyncDirection | None:
    text = _text(value).replace(" ", "").replace("_", "").replace("-", "").lower()
    if not text:
        return None
    if text in {"qolony→iquavis", "qolony>iquavis", "qolonytoiquavis", "qolonyからiquavis"}:
        return "qolony_to_iquavis"
    if text in {"iquavis→qolony", "iquavis>qolony", "iquavistoqolony", "iquavisからqolony"}:
        return "iquavis_to_qolony"
    return None


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _as_effective_iquavis_id(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().replace("\u3000", "").strip()
    if not text:
        return ""
    lowered = text.lower()
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
    return text


def _bool(value: Any, *, default: bool) -> bool:
    text = _text(value).lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "y", "on", "有効", "はい", "top"}:
        return True
    if text in {"0", "false", "no", "n", "off", "無効", "いいえ"}:
        return False
    return default


def _enabled(value: Any) -> bool:
    text = _text(value).lower()
    return text not in {"", "0", "false", "no", "n", "off", "skip", "無効"}


def read_member_rows(path: Path) -> list[LinkTargetMemberRow]:
    workbook = load_workbook(path, data_only=True, keep_vba=False)
    if MEMBER_SHEET_NAME not in workbook.sheetnames:
        return []
    sheet = workbook[MEMBER_SHEET_NAME]
    headers = [_text(cell.value).lower() for cell in sheet[1]]
    index_by_header = {header: index + 1 for index, header in enumerate(headers) if header}
    rows: list[LinkTargetMemberRow] = []

    for row_number in range(2, sheet.max_row + 1):
        values = {
            header: sheet.cell(row=row_number, column=column).value
            for header, column in index_by_header.items()
        }
        if not _enabled(values.get("use")):
            continue
        nickname = _text(values.get("nickname"))
        full_name = _text(values.get("full_name"))
        login_id = _text(values.get("login_id")) or None
        iquavis_user_id = _text(values.get("iquavis_user_id")) or None
        qolony_user_id = _text(values.get("qolony_user_id")) or None
        if not any([nickname, full_name, login_id, iquavis_user_id, qolony_user_id]):
            continue
        rows.append(
            LinkTargetMemberRow(
                qolony_user_id=qolony_user_id,
                nickname=nickname or full_name or login_id or iquavis_user_id or "",
                full_name=full_name,
                login_id=login_id,
                iquavis_user_id=iquavis_user_id,
                is_top_manager=_bool(values.get("is_top_manager"), default=False),
                is_active=_bool(values.get("is_active"), default=True),
                note=_text(values.get("note")),
            )
        )
    return rows


def read_sync_targets(path: Path) -> list[SyncedIquavisTarget]:
    if not path.exists():
        return []
    workbook = load_workbook(path, data_only=True, keep_vba=False)
    try:
        targets: list[SyncedIquavisTarget] = []
        targets.extend(
            _read_target_sheet(
                workbook,
                sheet_name=TASK_LINK_SHEET_NAME,
                id_header="iquavis_task_id",
                kind="task",
            )
        )
        targets.extend(
            _read_target_sheet(
                workbook,
                sheet_name=TASK_DOMAIN_LINK_SHEET_NAME,
                id_header="iquavis_task_domain_id",
                kind="task_domain",
            )
        )

        merged: dict[str, SyncedIquavisTarget] = {}
        for target in targets:
            iquavis_id = _as_effective_iquavis_id(target.iquavis_id)
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
    finally:
        workbook.close()


def clear_force_sync_requests(path: Path, iquavis_ids: list[str]) -> list[str]:
    target_ids = {
        _as_effective_iquavis_id(item)
        for item in iquavis_ids
        if _as_effective_iquavis_id(item)
    }
    if not path.exists() or not target_ids:
        return []
    workbook = load_workbook(path, data_only=False, keep_vba=False)
    try:
        cleared: list[str] = []
        for sheet_name, id_header in (
            (TASK_LINK_SHEET_NAME, "iquavis_task_id"),
            (TASK_DOMAIN_LINK_SHEET_NAME, "iquavis_task_domain_id"),
        ):
            if sheet_name not in workbook.sheetnames:
                continue
            sheet = workbook[sheet_name]
            headers = [_text(cell.value).lower() for cell in sheet[1]]
            index_by_header = {header: index + 1 for index, header in enumerate(headers) if header}
            id_col = index_by_header.get(id_header)
            force_col = index_by_header.get("force_sync")
            if id_col is None or force_col is None:
                continue
            for row_number in range(2, sheet.max_row + 1):
                iquavis_id = _as_effective_iquavis_id(
                    sheet.cell(row=row_number, column=id_col).value
                )
                if iquavis_id not in target_ids:
                    continue
                if _text(sheet.cell(row=row_number, column=force_col).value):
                    sheet.cell(row=row_number, column=force_col).value = ""
                    cleared.append(iquavis_id)
        if cleared:
            workbook.save(path)
        return list(dict.fromkeys(cleared))
    finally:
        workbook.close()


def _read_target_sheet(
    workbook: Workbook,
    *,
    sheet_name: str,
    id_header: str,
    kind: str,
) -> list[SyncedIquavisTarget]:
    if sheet_name not in workbook.sheetnames:
        return []
    sheet = workbook[sheet_name]
    headers = [_text(cell.value).lower() for cell in sheet[1]]
    index_by_header = {header: index + 1 for index, header in enumerate(headers) if header}
    id_col = index_by_header.get(id_header)
    if id_col is None:
        return []

    rows: list[SyncedIquavisTarget] = []
    for row_number in range(2, sheet.max_row + 1):
        values = {
            header: sheet.cell(row=row_number, column=column).value
            for header, column in index_by_header.items()
        }
        if not _enabled(values.get("use")):
            continue
        iquavis_id = _as_effective_iquavis_id(
            sheet.cell(row=row_number, column=id_col).value
        )
        if not iquavis_id:
            continue
        rows.append(
            SyncedIquavisTarget(
                iquavis_id=iquavis_id,
                kind=kind,  # type: ignore[arg-type]
                force_sync=_force_sync(values.get("force_sync")),
                qolony_task_id=_text(values.get("qolony_task_id")) or None,
                qolony_parent_task_id=_text(values.get("qolony_parent_task_id")) or None,
            )
        )
    return rows


def _ensure_sheet(workbook: Workbook, name: str, headers: list[str]) -> Worksheet:
    if name in workbook.sheetnames:
        sheet = workbook[name]
        sheet.delete_rows(1, sheet.max_row)
    else:
        sheet = workbook.create_sheet(name)
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill(fill_type="solid", fgColor="D9EAF7")
    return sheet


def write_member_sheet(path: Path, users: list[User]) -> None:
    if path.exists():
        workbook = load_workbook(path)
    else:
        workbook = Workbook()
    if workbook.active.title == "Sheet" and len(workbook.sheetnames) == 1:
        workbook.active.title = "README"
    if "README" in workbook.sheetnames:
        readme = workbook["README"]
    else:
        readme = workbook.create_sheet("README", 0)
    if readme.max_row == 1 and readme.cell(1, 1).value is None:
        readme.append(["key", "rule"])
    existing_readme_keys = {
        _text(readme.cell(row=row_number, column=1).value)
        for row_number in range(1, readme.max_row + 1)
    }
    if "member_sheet" not in existing_readme_keys:
        readme.append(["member_sheet", "QOLONY_members にメンバーID連携情報を記入し、QOLONYへ取り込みます。"])
    if "member_delete" not in existing_readme_keys:
        readme.append(["member_delete", "メンバーは削除しません。退任・異動は is_active=false で非表示化します。"])

    sheet = _ensure_sheet(workbook, MEMBER_SHEET_NAME, MEMBER_HEADERS)
    for user in sorted(users, key=lambda item: (getattr(item, "Nickname", "") or item.Name, item.Id)):
        sheet.append(
            [
                "1",
                user.Id,
                getattr(user, "Nickname", "") or user.Name,
                getattr(user, "FullName", "") or "",
                user.LoginId or "",
                user.IQuavisUserId or "",
                "1" if user.IsTopManager else "0",
                "1" if getattr(user, "IsActive", True) else "0",
                "",
            ]
        )

    for column_cells in sheet.columns:
        width = max(len(str(cell.value or "")) for cell in column_cells) + 2
        sheet.column_dimensions[column_cells[0].column_letter].width = min(max(width, 12), 48)
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
