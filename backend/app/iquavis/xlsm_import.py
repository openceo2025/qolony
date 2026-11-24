from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook


SELECTION_MARK = "●"


@dataclass(frozen=True)
class XlsmImportTaskRow:
    sheet_name: str
    row_number: int
    iquavis_task_id: str
    project_id: str | None
    remote_parent_task_id: str | None
    parent_override_local_task_id: str | None
    name: str
    task_type: str
    start_date: date
    end_date: date
    note: str
    progress_status: str | None
    progress_rate: int | None
    first_assign_iquavis_user_id: str | None
    first_assign_login_id: str | None
    first_assign_name: str | None
    creator_iquavis_user_id: str | None
    creator_login_id: str | None
    creator_name: str | None


@dataclass(frozen=True)
class XlsmImportSelection:
    rows: list[XlsmImportTaskRow]
    selected_count: int
    duplicate_count: int


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _parse_date_value(value: Any, *, label: str, sheet_name: str, row_number: int) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()

    text = _as_text(value)
    if not text:
        raise ValueError(f"{sheet_name} {row_number}行目: {label} が空です")
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ValueError(f"{sheet_name} {row_number}行目: {label} の日付形式が不正です: {text}") from exc


def _parse_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    text = _as_text(value)
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _parse_assigns(value: Any) -> tuple[str | None, str | None, str | None]:
    text = _as_text(value)
    if not text:
        return None, None, None

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None, None, None

    if not isinstance(payload, list) or not payload:
        return None, None, None

    first = payload[0]
    if not isinstance(first, dict):
        return None, None, None
    iquavis_user_id = _as_text(first.get("Id")) or None
    login_id = _as_text(first.get("LoginId")) or None
    name = _as_text(first.get("Name")) or None
    return iquavis_user_id, login_id, name


def _parse_user_reference(value: Any) -> tuple[str | None, str | None, str | None]:
    text = _as_text(value)
    if not text:
        return None, None, None

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text, text, text

    if isinstance(payload, list):
        payload = payload[0] if payload else None
    if not isinstance(payload, dict):
        return text, text, text

    iquavis_user_id = _as_text(payload.get("Id")) or _as_text(payload.get("UserId")) or None
    login_id = _as_text(payload.get("LoginId")) or None
    name = _as_text(payload.get("Name")) or None
    return iquavis_user_id, login_id, name


def _build_header_map(header_row: tuple[Any, ...], *, sheet_name: str) -> dict[str, int]:
    header_map: dict[str, int] = {}
    for idx, value in enumerate(header_row):
        key = _as_text(value)
        if key:
            header_map[key] = idx

    required_headers = ["Id", "Name", "Type", "StartDate", "EndDate", "読み取り対象", "親タスクId"]
    missing = [header for header in required_headers if header not in header_map]
    if missing:
        raise ValueError(f"{sheet_name}: 必須列が不足しています: {', '.join(missing)}")
    return header_map


def _get_row_value(row: tuple[Any, ...], header_map: dict[str, int], header_name: str) -> Any:
    idx = header_map.get(header_name)
    if idx is None or idx >= len(row):
        return None
    return row[idx]


def _get_first_row_value(row: tuple[Any, ...], header_map: dict[str, int], header_names: list[str]) -> Any:
    for header_name in header_names:
        value = _get_row_value(row, header_map, header_name)
        if _as_text(value):
            return value
    return None


def load_selected_tasks_from_xlsm(workbook_path: Path, sheet_names: list[str]) -> XlsmImportSelection:
    if not workbook_path.exists():
        raise FileNotFoundError(f"xlsm が見つかりません: {workbook_path}")
    if not sheet_names:
        raise ValueError("少なくとも1つのシート名を指定してください")

    workbook = load_workbook(workbook_path, read_only=True, data_only=True, keep_vba=True)
    try:
        missing_sheets = [sheet_name for sheet_name in sheet_names if sheet_name not in workbook.sheetnames]
        if missing_sheets:
            raise ValueError(f"指定シートが見つかりません: {', '.join(missing_sheets)}")

        rows: list[XlsmImportTaskRow] = []
        seen_task_ids: set[str] = set()
        selected_count = 0
        duplicate_count = 0

        for sheet_name in sheet_names:
            worksheet = workbook[sheet_name]
            header_row = next(worksheet.iter_rows(min_row=1, max_row=1, values_only=True), None)
            if header_row is None:
                raise ValueError(f"{sheet_name}: ヘッダー行がありません")
            header_map = _build_header_map(header_row, sheet_name=sheet_name)

            for row_number, row in enumerate(worksheet.iter_rows(min_row=2, values_only=True), start=2):
                if _as_text(_get_row_value(row, header_map, "読み取り対象")) != SELECTION_MARK:
                    continue

                selected_count += 1
                iquavis_task_id = _as_text(_get_row_value(row, header_map, "Id"))
                if not iquavis_task_id:
                    raise ValueError(f"{sheet_name} {row_number}行目: Id が空です")
                if iquavis_task_id in seen_task_ids:
                    duplicate_count += 1
                    continue

                name = _as_text(_get_row_value(row, header_map, "Name"))
                if not name:
                    raise ValueError(f"{sheet_name} {row_number}行目: Name が空です")

                task_type = _as_text(_get_row_value(row, header_map, "Type")) or "Task"
                if task_type not in {"Task", "GroupTask"}:
                    task_type = "Task"

                start_date = _parse_date_value(
                    _get_row_value(row, header_map, "StartDate"),
                    label="StartDate",
                    sheet_name=sheet_name,
                    row_number=row_number,
                )
                end_date = _parse_date_value(
                    _get_row_value(row, header_map, "EndDate"),
                    label="EndDate",
                    sheet_name=sheet_name,
                    row_number=row_number,
                )

                assign_user_id, login_id, assign_name = _parse_assigns(_get_row_value(row, header_map, "Assigns"))
                creator_from_record = _parse_user_reference(
                    _get_first_row_value(row, header_map, ["CreateUser", "Creator", "CreatedBy"])
                )
                creator_user_id = (
                    _as_text(
                        _get_first_row_value(
                            row,
                            header_map,
                            ["CreateUserId", "CreatorUserId", "CreatedUserId", "CreatedByUserId"],
                        )
                    )
                    or creator_from_record[0]
                )
                creator_login_id = (
                    _as_text(
                        _get_first_row_value(
                            row,
                            header_map,
                            ["CreateUserLoginId", "CreatorLoginId", "CreatedByLoginId"],
                        )
                    )
                    or creator_from_record[1]
                )
                creator_name = (
                    _as_text(
                        _get_first_row_value(
                            row,
                            header_map,
                            ["CreateUserName", "CreatorName", "CreatedByName"],
                        )
                    )
                    or creator_from_record[2]
                )

                rows.append(
                    XlsmImportTaskRow(
                        sheet_name=sheet_name,
                        row_number=row_number,
                        iquavis_task_id=iquavis_task_id,
                        project_id=_as_text(_get_row_value(row, header_map, "ProjectId")) or None,
                        remote_parent_task_id=_as_text(_get_row_value(row, header_map, "ParentTaskId")) or None,
                        parent_override_local_task_id=_as_text(_get_row_value(row, header_map, "親タスクId")) or None,
                        name=name,
                        task_type=task_type,
                        start_date=start_date,
                        end_date=end_date,
                        note=_as_text(_get_row_value(row, header_map, "Note")),
                        progress_status=_as_text(_get_row_value(row, header_map, "Progress.Status")) or None,
                        progress_rate=_parse_optional_int(_get_row_value(row, header_map, "Progress.ProgressRate")),
                        first_assign_iquavis_user_id=assign_user_id,
                        first_assign_login_id=login_id,
                        first_assign_name=assign_name,
                        creator_iquavis_user_id=creator_user_id or None,
                        creator_login_id=creator_login_id or None,
                        creator_name=creator_name or None,
                    )
                )
                seen_task_ids.add(iquavis_task_id)

        return XlsmImportSelection(
            rows=rows,
            selected_count=selected_count,
            duplicate_count=duplicate_count,
        )
    finally:
        workbook.close()
