from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.workbook import Workbook


GUID_PATTERN = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
DATA_ID_RE = re.compile(rf"(?:^|[?&])DataId=({GUID_PATTERN})(?:&|$)", re.IGNORECASE)
GUID_RE = re.compile(GUID_PATTERN)
ILLEGAL_EXCEL_CHAR_RE = re.compile(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]")

TASK_HEADERS = [
    "use",
    "iquavis_task_id",
    "qolony_task_id",
    "qolony_parent_task_id",
    "force_sync",
    "note",
]
DOMAIN_HEADERS = [
    "use",
    "iquavis_task_domain_id",
    "qolony_task_id",
    "qolony_parent_task_id",
    "force_sync",
    "note",
]
RAW_MEMO_HEADERS = [
    "memo_text",
    "normalized_id",
    "target_kind",
    "duplicate_group",
    "note",
]
REPORT_HEADERS = ["key", "value"]


@dataclass(frozen=True)
class RawEntry:
    line_no: int
    text: str
    normalized_id: str
    extraction_mode: str


@dataclass(frozen=True)
class TaskRow:
    task_id: str
    name: str
    task_type: str
    project_id: str
    project_group: str
    sheet_name: str
    row_no: int
    task_domain_id: str
    task_domain_path: tuple[str, ...]
    task_domain_path_ids: tuple[str, ...]
    parent_task_id: str
    start_date: str
    end_date: str
    assigned_man_hour: str


@dataclass(frozen=True)
class DomainRow:
    domain_id: str
    name: str
    full_name: str
    project_id: str
    project_group: str
    sheet_name: str
    row_no: int
    parent_task_domain_id: str


def normalize_guid(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    match = GUID_RE.search(text)
    return match.group(0).lower() if match else text.lower()


def clean_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
    return ILLEGAL_EXCEL_CHAR_RE.sub("", text)


def project_group_from_sheet(sheet_name: str) -> str:
    if "\u500b\u4eba" in sheet_name:
        return "personal"
    if "\u5168\u4f53" in sheet_name:
        return "overall"
    return "unknown"


def read_text_file(path: Path) -> str:
    for encoding in ("utf-8-sig", "cp932", "utf-16"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text()


def extract_raw_entries(text: str) -> list[RawEntry]:
    entries: list[RawEntry] = []
    for index, line in enumerate(text.splitlines(), start=1):
        memo = clean_text(line)
        if not memo:
            continue

        data_id_match = DATA_ID_RE.search(memo)
        if data_id_match:
            entries.append(
                RawEntry(
                    line_no=index,
                    text=memo,
                    normalized_id=normalize_guid(data_id_match.group(1)),
                    extraction_mode="DataId",
                )
            )
            continue

        # URLではなくIDだけをメモした場合は、行内の最後のGUIDを候補として扱う。
        guid_matches = GUID_RE.findall(memo)
        if guid_matches:
            entries.append(
                RawEntry(
                    line_no=index,
                    text=memo,
                    normalized_id=normalize_guid(guid_matches[-1]),
                    extraction_mode="fallback_guid",
                )
            )
            continue

        entries.append(RawEntry(line_no=index, text=memo, normalized_id="", extraction_mode="no_id"))
    return entries


def unique_ids(entries: Iterable[RawEntry]) -> list[str]:
    ordered: OrderedDict[str, None] = OrderedDict()
    for entry in entries:
        if entry.normalized_id:
            ordered.setdefault(entry.normalized_id, None)
    return list(ordered.keys())


def header_index_map(header_values: tuple[object, ...]) -> dict[str, int]:
    return {clean_text(value): index for index, value in enumerate(header_values) if value is not None}


def get_row_value(row_values: tuple[object, ...], headers: dict[str, int], name: str) -> str:
    index = headers.get(name)
    if index is None or index >= len(row_values):
        return ""
    return clean_text(row_values[index])


def read_task_rows(xlsm_path: Path) -> tuple[dict[str, TaskRow], dict[str, list[TaskRow]], list[str]]:
    workbook = load_workbook(xlsm_path, data_only=True, read_only=True, keep_vba=True)
    task_by_id: dict[str, TaskRow] = {}
    duplicates: dict[str, list[TaskRow]] = defaultdict(list)
    scanned_sheets: list[str] = []

    try:
        for ws in workbook.worksheets:
            if ws.sheet_state != "visible" or "\u5168\u30bf\u30b9\u30af" not in ws.title:
                continue
            rows = ws.iter_rows(values_only=True)
            header_values = next(rows, None)
            if header_values is None:
                continue
            headers = header_index_map(header_values)
            if "Id" not in headers or "TaskDomainId" not in headers:
                continue

            scanned_sheets.append(ws.title)
            project_group = project_group_from_sheet(ws.title)
            for row_no, row_values in enumerate(rows, start=2):
                task_id = normalize_guid(get_row_value(row_values, headers, "Id"))
                if not task_id:
                    continue

                path_labels = tuple(
                    label
                    for label in (get_row_value(row_values, headers, f"taskdomain{i}") for i in range(1, 11))
                    if label
                )
                path_ids = []
                for i in range(1, 11):
                    path_id = normalize_guid(get_row_value(row_values, headers, f"__TaskDomainId{i}"))
                    if path_id and path_id not in path_ids:
                        path_ids.append(path_id)
                task_domain_id = normalize_guid(get_row_value(row_values, headers, "TaskDomainId"))
                if task_domain_id and task_domain_id not in path_ids:
                    path_ids.append(task_domain_id)

                task = TaskRow(
                    task_id=task_id,
                    name=get_row_value(row_values, headers, "Name"),
                    task_type=get_row_value(row_values, headers, "Type"),
                    project_id=normalize_guid(get_row_value(row_values, headers, "ProjectId")),
                    project_group=project_group,
                    sheet_name=ws.title,
                    row_no=row_no,
                    task_domain_id=task_domain_id,
                    task_domain_path=path_labels,
                    task_domain_path_ids=tuple(path_ids),
                    parent_task_id=normalize_guid(get_row_value(row_values, headers, "ParentTaskId")),
                    start_date=get_row_value(row_values, headers, "StartDate"),
                    end_date=get_row_value(row_values, headers, "EndDate"),
                    assigned_man_hour=get_row_value(row_values, headers, "AssignedManHour"),
                )

                if task_id in task_by_id:
                    duplicates[task_id].append(task)
                    continue
                task_by_id[task_id] = task
    finally:
        workbook.close()

    return task_by_id, duplicates, scanned_sheets


def read_domain_rows(
    xlsm_path: Path,
) -> tuple[
    dict[tuple[str, str], DomainRow],
    dict[str, DomainRow],
    dict[str, list[DomainRow]],
    list[str],
]:
    workbook = load_workbook(xlsm_path, data_only=True, read_only=True, keep_vba=True)
    domain_by_key: dict[tuple[str, str], DomainRow] = {}
    domain_by_id: dict[str, DomainRow] = {}
    duplicates_by_id: dict[str, list[DomainRow]] = defaultdict(list)
    scanned_sheets: list[str] = []

    try:
        for ws in workbook.worksheets:
            is_domain_sheet = "\u30bf\u30b9\u30af\u30c9\u30e1\u30a4\u30f3" in ws.title
            is_task_sheet = "\u5168\u30bf\u30b9\u30af" in ws.title
            if ws.sheet_state != "visible" or not is_domain_sheet or is_task_sheet:
                continue
            rows = ws.iter_rows(values_only=True)
            header_values = next(rows, None)
            if header_values is None:
                continue
            headers = header_index_map(header_values)
            if "Id" not in headers or "Name" not in headers:
                continue

            scanned_sheets.append(ws.title)
            project_group = project_group_from_sheet(ws.title)
            for row_no, row_values in enumerate(rows, start=2):
                domain_id = normalize_guid(get_row_value(row_values, headers, "Id"))
                if not domain_id:
                    continue
                domain = DomainRow(
                    domain_id=domain_id,
                    name=get_row_value(row_values, headers, "Name"),
                    full_name=get_row_value(row_values, headers, "FullName"),
                    project_id=normalize_guid(get_row_value(row_values, headers, "ProjectId")),
                    project_group=project_group,
                    sheet_name=ws.title,
                    row_no=row_no,
                    parent_task_domain_id=normalize_guid(get_row_value(row_values, headers, "ParentTaskDomainId")),
                )
                key = (project_group, domain_id)
                if key in domain_by_key:
                    duplicates_by_id[domain_id].append(domain)
                    continue
                if domain_id in domain_by_id:
                    duplicates_by_id[domain_id].append(domain)
                domain_by_key[key] = domain
                domain_by_id.setdefault(domain_id, domain)
    finally:
        workbook.close()

    return domain_by_key, domain_by_id, duplicates_by_id, scanned_sheets


def reset_sheet(workbook: Workbook, sheet_name: str, headers: list[str]):
    ws = workbook[sheet_name] if sheet_name in workbook.sheetnames else workbook.create_sheet(sheet_name)
    ws.delete_rows(1, ws.max_row)
    ws.append(headers)
    return ws


def style_sheet(ws, widths: dict[str, int]) -> None:
    header_fill = PatternFill("solid", fgColor="D9EAF7")
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(vertical="top", wrap_text=True)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for index, header in enumerate((cell.value for cell in ws[1]), start=1):
        width = widths.get(str(header), 18)
        ws.column_dimensions[get_column_letter(index)].width = width
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)


def collect_domain_chain(
    task: TaskRow,
    domain_by_key: dict[tuple[str, str], DomainRow],
) -> tuple[str, ...]:
    leaf_id = task.task_domain_id or (task.task_domain_path_ids[-1] if task.task_domain_path_ids else "")
    if not leaf_id:
        return task.task_domain_path_ids

    chain_leaf_to_root: list[str] = []
    seen: set[str] = set()
    current_id = leaf_id
    while current_id:
        if current_id in seen:
            break
        seen.add(current_id)
        chain_leaf_to_root.append(current_id)
        domain = domain_by_key.get((task.project_group, current_id))
        if domain is None or not domain.parent_task_domain_id:
            break
        current_id = domain.parent_task_domain_id

    chain = tuple(reversed(chain_leaf_to_root))
    if len(chain) == 1 and len(task.task_domain_path_ids) > 1:
        return task.task_domain_path_ids
    return chain


def task_note(task: TaskRow, source_lines: list[int]) -> str:
    domain_path = " > ".join(task.task_domain_path)
    parts = [
        f"source_txt_lines={','.join(str(line) for line in source_lines[:10])}",
        f"project_group={task.project_group}",
        f"source_sheet={task.sheet_name}",
        f"source_row={task.row_no}",
        f"project_id={task.project_id}",
        f"name={task.name}",
    ]
    if domain_path:
        parts.append(f"domain_path={domain_path}")
    if task.parent_task_id:
        parts.append(f"iquavis_parent_task_id={task.parent_task_id}")
    if task.assigned_man_hour:
        parts.append(f"AssignedManHour={task.assigned_man_hour}")
    return clean_text("; ".join(parts))


def domain_note(
    domain: DomainRow | None,
    *,
    project_group: str,
    referenced_task_ids: list[str],
    fallback_path: str = "",
    parent_override: str = "",
) -> str:
    parts = [f"project_group={project_group}"]
    if domain is None:
        parts.append("domain_row_missing_in_project_sheet")
    else:
        parts.extend(
            [
                f"source_sheet={domain.sheet_name}",
                f"source_row={domain.row_no}",
                f"project_id={domain.project_id}",
                f"name={domain.name}",
            ]
        )
        if domain.full_name:
            parts.append(f"full_name={domain.full_name}")
        if domain.parent_task_domain_id:
            parts.append(f"parent_task_domain_id={domain.parent_task_domain_id}")
    if fallback_path:
        parts.append(f"task_domain_path={fallback_path}")
    if parent_override:
        parts.append(f"qolony_parent_override={parent_override}")
    if referenced_task_ids:
        parts.append(f"referenced_by_tasks={','.join(referenced_task_ids[:8])}")
    return clean_text("; ".join(parts))


def write_output_workbook(
    output_path: Path,
    entries: list[RawEntry],
    ordered_ids: list[str],
    task_by_id: dict[str, TaskRow],
    domain_by_key: dict[tuple[str, str], DomainRow],
    domain_by_id: dict[str, DomainRow],
    task_scanned_sheets: list[str],
    domain_scanned_sheets: list[str],
    task_duplicates: dict[str, list[TaskRow]],
    domain_duplicates: dict[str, list[DomainRow]],
    personal_root_task_id: str = "",
) -> dict[str, int | str]:
    workbook = load_workbook(output_path) if output_path.exists() else Workbook()
    if "Sheet" in workbook.sheetnames and len(workbook.sheetnames) == 1:
        del workbook["Sheet"]

    line_numbers_by_id: dict[str, list[int]] = defaultdict(list)
    for entry in entries:
        if entry.normalized_id:
            line_numbers_by_id[entry.normalized_id].append(entry.line_no)

    matched_task_ids = [task_id for task_id in ordered_ids if task_id in task_by_id]
    missing_task_ids = [task_id for task_id in ordered_ids if task_id not in task_by_id]

    domain_ids: OrderedDict[str, None] = OrderedDict()
    domain_group_by_id: dict[str, str] = {}
    domain_paths_by_id: dict[str, str] = {}
    referenced_tasks_by_domain: dict[str, list[str]] = defaultdict(list)
    personal_top_domain_ids: set[str] = set()
    domain_group_conflicts: set[str] = set()
    task_counts_by_group = Counter(task_by_id[task_id].project_group for task_id in matched_task_ids)

    for task_id in matched_task_ids:
        task = task_by_id[task_id]
        chain = collect_domain_chain(task, domain_by_key)
        if task.project_group == "personal" and chain:
            personal_top_domain_ids.add(chain[0])
        path_label = " > ".join(task.task_domain_path)
        for domain_id in chain:
            domain_ids.setdefault(domain_id, None)
            existing_group = domain_group_by_id.get(domain_id)
            if existing_group is not None and existing_group != task.project_group:
                domain_group_conflicts.add(domain_id)
            domain_group_by_id.setdefault(domain_id, task.project_group)
            if path_label:
                domain_paths_by_id.setdefault(domain_id, path_label)
            referenced_tasks_by_domain[domain_id].append(task_id)

    missing_domain_ids = []
    for domain_id in domain_ids:
        group = domain_group_by_id.get(domain_id, "unknown")
        if (group, domain_id) not in domain_by_key and domain_id not in domain_by_id:
            missing_domain_ids.append(domain_id)

    ws_tasks = reset_sheet(workbook, "QOLONY_linked_tasks", TASK_HEADERS)
    for task_id in matched_task_ids:
        task = task_by_id[task_id]
        ws_tasks.append(["1", task.task_id, "", "", "", task_note(task, line_numbers_by_id[task_id])])

    ws_domains = reset_sheet(workbook, "QOLONY_linked_task_domains", DOMAIN_HEADERS)
    domain_counts_by_group = Counter(domain_group_by_id.values())
    for domain_id in domain_ids:
        group = domain_group_by_id.get(domain_id, "unknown")
        domain = domain_by_key.get((group, domain_id)) or domain_by_id.get(domain_id)
        parent_override = personal_root_task_id if group == "personal" and domain_id in personal_top_domain_ids else ""
        note = domain_note(
            domain,
            project_group=group,
            referenced_task_ids=referenced_tasks_by_domain[domain_id],
            fallback_path=domain_paths_by_id.get(domain_id, ""),
            parent_override=parent_override,
        )
        ws_domains.append(["1", domain_id, "", parent_override, "", note])

    counts_by_id = Counter(entry.normalized_id for entry in entries if entry.normalized_id)
    first_line_by_id: dict[str, int] = {}
    for entry in entries:
        if entry.normalized_id and entry.normalized_id not in first_line_by_id:
            first_line_by_id[entry.normalized_id] = entry.line_no

    ws_raw = reset_sheet(workbook, "raw_memo", RAW_MEMO_HEADERS)
    for entry in entries:
        if not entry.normalized_id:
            duplicate_group = ""
            note = f"ID抽出対象外; extraction_mode={entry.extraction_mode}"
        else:
            duplicate_group = entry.normalized_id if counts_by_id[entry.normalized_id] > 1 else ""
            if entry.normalized_id in task_by_id:
                task = task_by_id[entry.normalized_id]
                note = (
                    f"matched; project_group={task.project_group}; sheet={task.sheet_name}; "
                    f"row={task.row_no}; extraction_mode={entry.extraction_mode}"
                )
            else:
                note = f"missing_in_task_sheets; extraction_mode={entry.extraction_mode}"
            if counts_by_id[entry.normalized_id] > 1 and first_line_by_id[entry.normalized_id] != entry.line_no:
                note += f"; duplicate_of_line={first_line_by_id[entry.normalized_id]}"
        ws_raw.append([entry.text, entry.normalized_id, "task" if entry.normalized_id else "", duplicate_group, clean_text(note)])

    ws_report = reset_sheet(workbook, "link_build_report", REPORT_HEADERS)
    report_rows: list[tuple[str, str | int]] = [
        ("source_txt_line_count", len(entries)),
        ("raw_id_count", sum(1 for entry in entries if entry.normalized_id)),
        ("unique_task_id_count", len(ordered_ids)),
        ("duplicate_removed_count", sum(counts_by_id.values()) - len(ordered_ids)),
        ("matched_task_count", len(matched_task_ids)),
        ("missing_task_count", len(missing_task_ids)),
        ("overall_task_count", task_counts_by_group["overall"]),
        ("personal_task_count", task_counts_by_group["personal"]),
        ("unknown_task_count", task_counts_by_group["unknown"]),
        ("linked_task_domain_count", len(domain_ids)),
        ("overall_task_domain_count", domain_counts_by_group["overall"]),
        ("personal_task_domain_count", domain_counts_by_group["personal"]),
        ("unknown_task_domain_count", domain_counts_by_group["unknown"]),
        ("personal_root_domain_count", len(personal_top_domain_ids)),
        ("personal_root_task_id", personal_root_task_id),
        ("missing_task_domain_count", len(missing_domain_ids)),
        ("domain_group_conflict_count", len(domain_group_conflicts)),
        ("scanned_task_sheets", ", ".join(task_scanned_sheets)),
        ("scanned_task_domain_sheets", ", ".join(domain_scanned_sheets)),
        ("duplicate_task_rows_in_xlsm", str(len(task_duplicates))),
        ("duplicate_domain_rows_in_xlsm", str(len(domain_duplicates))),
    ]
    for key, value in report_rows:
        ws_report.append([key, value])
    if missing_task_ids:
        ws_report.append(["missing_task_ids", ", ".join(missing_task_ids)])
    if missing_domain_ids:
        ws_report.append(["missing_task_domain_ids", ", ".join(missing_domain_ids)])
    if domain_group_conflicts:
        ws_report.append(["domain_group_conflict_ids", ", ".join(sorted(domain_group_conflicts))])
    if personal_top_domain_ids:
        ws_report.append(["personal_top_domain_ids", ", ".join(sorted(personal_top_domain_ids))])

    style_sheet(
        ws_tasks,
        {
            "use": 8,
            "iquavis_task_id": 40,
            "qolony_task_id": 20,
            "qolony_parent_task_id": 24,
            "force_sync": 18,
            "note": 120,
        },
    )
    style_sheet(
        ws_domains,
        {
            "use": 8,
            "iquavis_task_domain_id": 40,
            "qolony_task_id": 20,
            "qolony_parent_task_id": 40,
            "force_sync": 18,
            "note": 120,
        },
    )
    style_sheet(
        ws_raw,
        {
            "memo_text": 120,
            "normalized_id": 40,
            "target_kind": 14,
            "duplicate_group": 40,
            "note": 80,
        },
    )
    style_sheet(ws_report, {"key": 34, "value": 140})

    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)

    return {
        "source_txt_line_count": len(entries),
        "raw_id_count": sum(1 for entry in entries if entry.normalized_id),
        "unique_task_id_count": len(ordered_ids),
        "duplicate_removed_count": sum(counts_by_id.values()) - len(ordered_ids),
        "matched_task_count": len(matched_task_ids),
        "overall_task_count": task_counts_by_group["overall"],
        "personal_task_count": task_counts_by_group["personal"],
        "missing_task_count": len(missing_task_ids),
        "linked_task_domain_count": len(domain_ids),
        "overall_task_domain_count": domain_counts_by_group["overall"],
        "personal_task_domain_count": domain_counts_by_group["personal"],
        "personal_root_domain_count": len(personal_top_domain_ids),
        "missing_task_domain_count": len(missing_domain_ids),
        "domain_group_conflict_count": len(domain_group_conflicts),
        "personal_root_task_id": personal_root_task_id,
        "output_path": str(output_path),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build QOLONY iQUAVIS link target workbook from URL memo text.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--xlsm", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--personal-root-task-id", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = args.input
    xlsm_path = args.xlsm
    output_path = args.output

    if not input_path.exists():
        print(f"input not found: {input_path}", file=sys.stderr)
        return 2
    if not xlsm_path.exists():
        print(f"xlsm not found: {xlsm_path}", file=sys.stderr)
        return 2

    entries = extract_raw_entries(read_text_file(input_path))
    ordered_ids = unique_ids(entries)
    task_by_id, task_duplicates, task_scanned_sheets = read_task_rows(xlsm_path)
    domain_by_key, domain_by_id, domain_duplicates, domain_scanned_sheets = read_domain_rows(xlsm_path)

    summary = write_output_workbook(
        output_path=output_path,
        entries=entries,
        ordered_ids=ordered_ids,
        task_by_id=task_by_id,
        domain_by_key=domain_by_key,
        domain_by_id=domain_by_id,
        task_scanned_sheets=task_scanned_sheets,
        domain_scanned_sheets=domain_scanned_sheets,
        task_duplicates=task_duplicates,
        domain_duplicates=domain_duplicates,
        personal_root_task_id=str(args.personal_root_task_id).strip(),
    )

    for key, value in summary.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
