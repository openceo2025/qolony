from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.iquavis.link_targets import TASK_DOMAIN_LINK_SHEET_NAME, TASK_LINK_SHEET_NAME


TASK_HEADERS = [
    "use",
    "iquavis_task_id",
    "qolony_task_id",
    "qolony_parent_task_id",
    "force_sync",
    "note",
]


@dataclass(frozen=True)
class IquavisLinkTargetDiscoveryResult:
    linked_task_domain_count: int
    descendant_task_domain_count: int
    candidate_task_count: int
    already_registered_task_count: int
    appended_task_count: int
    appended_task_ids: list[str]


@dataclass(frozen=True)
class _TaskRow:
    task_id: str
    name: str
    task_domain_id: str
    parent_task_id: str
    start_date: date | None
    end_date: date | None
    source_sheet: str
    source_row: int


@dataclass(frozen=True)
class _TaskDomainRow:
    domain_id: str
    name: str
    parent_task_domain_id: str


def append_tasks_under_linked_domains(
    *,
    editor_workbook_path: Path,
    link_targets_workbook_path: Path,
    start_date_from: date,
) -> IquavisLinkTargetDiscoveryResult:
    if not editor_workbook_path.exists():
        raise FileNotFoundError(f"iquavis editor workbook not found: {editor_workbook_path}")
    if not link_targets_workbook_path.exists():
        raise FileNotFoundError(f"link targets workbook not found: {link_targets_workbook_path}")

    linked_domain_ids = _read_linked_domain_ids(link_targets_workbook_path)
    registered_task_ids = _read_registered_task_ids(link_targets_workbook_path)
    task_domains = _read_task_domains(editor_workbook_path)
    descendant_domain_ids = _collect_descendant_domain_ids(linked_domain_ids, task_domains)
    tasks = _read_tasks(editor_workbook_path)

    candidates = [
        task
        for task in tasks
        if task.task_domain_id in descendant_domain_ids
        and task.start_date is not None
        and task.start_date >= start_date_from
    ]
    appended = [task for task in candidates if task.task_id not in registered_task_ids]

    if appended:
        _append_task_rows(link_targets_workbook_path, appended, start_date_from)

    return IquavisLinkTargetDiscoveryResult(
        linked_task_domain_count=len(linked_domain_ids),
        descendant_task_domain_count=len(descendant_domain_ids),
        candidate_task_count=len(candidates),
        already_registered_task_count=len(candidates) - len(appended),
        appended_task_count=len(appended),
        appended_task_ids=[task.task_id for task in appended],
    )


def _read_linked_domain_ids(path: Path) -> set[str]:
    workbook = load_workbook(path, data_only=True, keep_vba=False)
    try:
        if TASK_DOMAIN_LINK_SHEET_NAME not in workbook.sheetnames:
            return set()
        sheet = workbook[TASK_DOMAIN_LINK_SHEET_NAME]
        headers = _header_map([cell.value for cell in sheet[1]])
        id_col = headers.get("iquavistaskdomainid")
        use_col = headers.get("use")
        if id_col is None:
            return set()
        domain_ids: set[str] = set()
        for row_number in range(2, sheet.max_row + 1):
            if use_col is not None and not _enabled(sheet.cell(row_number, use_col).value):
                continue
            domain_id = _normalize_id(sheet.cell(row_number, id_col).value)
            if domain_id:
                domain_ids.add(domain_id)
        return domain_ids
    finally:
        workbook.close()


def _read_registered_task_ids(path: Path) -> set[str]:
    workbook = load_workbook(path, data_only=True, keep_vba=False)
    try:
        if TASK_LINK_SHEET_NAME not in workbook.sheetnames:
            return set()
        sheet = workbook[TASK_LINK_SHEET_NAME]
        headers = _header_map([cell.value for cell in sheet[1]])
        id_col = headers.get("iquavistaskid")
        if id_col is None:
            return set()
        task_ids: set[str] = set()
        for row_number in range(2, sheet.max_row + 1):
            task_id = _normalize_id(sheet.cell(row_number, id_col).value)
            if task_id:
                task_ids.add(task_id)
        return task_ids
    finally:
        workbook.close()


def _read_task_domains(path: Path) -> dict[str, _TaskDomainRow]:
    workbook = load_workbook(path, read_only=True, data_only=True, keep_vba=True)
    try:
        domains: dict[str, _TaskDomainRow] = {}
        for sheet in workbook.worksheets:
            if sheet.sheet_state != "visible" or sheet.title.startswith("__"):
                continue
            rows = sheet.iter_rows(values_only=True)
            header_values = next(rows, None)
            if header_values is None:
                continue
            headers = _header_map(header_values)
            if not {"id", "name", "parenttaskdomainid"}.issubset(headers):
                continue
            for row in rows:
                domain_id = _normalize_id(_value(row, headers.get("id")))
                if not domain_id:
                    continue
                domains.setdefault(
                    domain_id,
                    _TaskDomainRow(
                        domain_id=domain_id,
                        name=_text(_value(row, headers.get("name"))),
                        parent_task_domain_id=_normalize_id(_value(row, headers.get("parenttaskdomainid"))),
                    ),
                )
        return domains
    finally:
        workbook.close()


def _collect_descendant_domain_ids(
    linked_domain_ids: set[str],
    domains: dict[str, _TaskDomainRow],
) -> set[str]:
    descendant_ids = set(linked_domain_ids)
    changed = True
    while changed:
        changed = False
        for domain in domains.values():
            if domain.parent_task_domain_id in descendant_ids and domain.domain_id not in descendant_ids:
                descendant_ids.add(domain.domain_id)
                changed = True
    return descendant_ids


def _read_tasks(path: Path) -> list[_TaskRow]:
    workbook = load_workbook(path, read_only=True, data_only=True, keep_vba=True)
    try:
        tasks: dict[str, _TaskRow] = {}
        for sheet in workbook.worksheets:
            # __TASKS 系は変更前情報なので、候補抽出では現在表示シートだけを正とする。
            if sheet.sheet_state != "visible" or sheet.title.startswith("__"):
                continue
            rows = sheet.iter_rows(values_only=True)
            header_values = next(rows, None)
            if header_values is None:
                continue
            headers = _header_map(header_values)
            if not {"id", "name", "startdate", "enddate", "taskdomainid"}.issubset(headers):
                continue
            for row_number, row in enumerate(rows, start=2):
                task_id = _normalize_id(_value(row, headers.get("id")))
                name = _text(_value(row, headers.get("name")))
                if not task_id or not name:
                    continue
                tasks.setdefault(
                    task_id,
                    _TaskRow(
                        task_id=task_id,
                        name=name,
                        task_domain_id=_normalize_id(_value(row, headers.get("taskdomainid"))),
                        parent_task_id=_normalize_id(_value(row, headers.get("parenttaskid"))),
                        start_date=_parse_date(_value(row, headers.get("startdate"))),
                        end_date=_parse_date(_value(row, headers.get("enddate"))),
                        source_sheet=sheet.title,
                        source_row=row_number,
                    ),
                )
        return list(tasks.values())
    finally:
        workbook.close()


def _append_task_rows(path: Path, tasks: list[_TaskRow], start_date_from: date) -> None:
    workbook = load_workbook(path) if path.exists() else Workbook()
    if "Sheet" in workbook.sheetnames and len(workbook.sheetnames) == 1:
        workbook.active.title = "README"

    sheet = workbook[TASK_LINK_SHEET_NAME] if TASK_LINK_SHEET_NAME in workbook.sheetnames else workbook.create_sheet(TASK_LINK_SHEET_NAME)
    if sheet.max_row == 1 and sheet.cell(1, 1).value is None:
        sheet.append(TASK_HEADERS)
    headers = _header_map([cell.value for cell in sheet[1]])
    for header in TASK_HEADERS:
        if _normalize_header(header) not in headers:
            sheet.cell(1, sheet.max_column + 1).value = header
            headers = _header_map([cell.value for cell in sheet[1]])

    for task in sorted(tasks, key=lambda item: (item.start_date or date.min, item.end_date or date.min, item.name, item.task_id)):
        row = [""] * sheet.max_column
        values = {
            "use": "1",
            "iquavis_task_id": task.task_id,
            "qolony_task_id": "",
            "qolony_parent_task_id": "",
            "force_sync": "",
            "note": _task_note(task, start_date_from),
        }
        for key, value in values.items():
            col = headers.get(_normalize_header(key))
            if col is not None:
                row[col - 1] = value
        sheet.append(row)

    _style_link_task_sheet(sheet)
    workbook.save(path)
    workbook.close()


def _task_note(task: _TaskRow, start_date_from: date) -> str:
    parts = [
        "auto_added_by=discover_iquavis_domain_tasks",
        f"filter=StartDate>={start_date_from.isoformat()}",
        f"source_sheet={task.source_sheet}",
        f"source_row={task.source_row}",
        f"name={task.name}",
        f"task_domain_id={task.task_domain_id}",
    ]
    if task.parent_task_id:
        parts.append(f"parent_task_id={task.parent_task_id}")
    if task.start_date:
        parts.append(f"start_date={task.start_date.isoformat()}")
    if task.end_date:
        parts.append(f"end_date={task.end_date.isoformat()}")
    return "; ".join(parts)


def _style_link_task_sheet(sheet: Any) -> None:
    header_fill = PatternFill("solid", fgColor="D9EAF7")
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(vertical="top", wrap_text=True)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    widths = {
        "use": 8,
        "iquavis_task_id": 40,
        "qolony_task_id": 20,
        "qolony_parent_task_id": 24,
        "force_sync": 18,
        "note": 120,
    }
    for index, cell in enumerate(sheet[1], start=1):
        sheet.column_dimensions[get_column_letter(index)].width = widths.get(str(cell.value), 18)
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)


def _header_map(values: Any) -> dict[str, int]:
    return {_normalize_header(value): index for index, value in enumerate(values, start=1) if _normalize_header(value)}


def _normalize_header(value: Any) -> str:
    return _text(value).replace(" ", "").replace("_", "").replace("-", "").lower()


def _normalize_id(value: Any) -> str:
    return _text(value).lower()


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _enabled(value: Any) -> bool:
    text = _text(value).lower()
    return text not in {"", "0", "false", "no", "n", "off", "skip"}


def _value(row: tuple[Any, ...], column: int | None) -> Any:
    if column is None:
        return None
    index = column - 1
    if index < 0 or index >= len(row):
        return None
    return row[index]


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10].replace("/", "-"))
    except ValueError:
        return None
