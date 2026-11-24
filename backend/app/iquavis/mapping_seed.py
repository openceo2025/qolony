from __future__ import annotations

import re
import sqlite3
import unicodedata
import zipfile
import posixpath
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet


DEFAULT_TASK_SHEET_NAMES = [
    "551個人業務計画 (1)",
    "551_全体日程_20240401",
]

EXCLUDED_VISUAL_SHEET_NAMES = {
    "project",
    "_original",
    "tasks_template",
    "Sheet3",
    *DEFAULT_TASK_SHEET_NAMES,
}


@dataclass(frozen=True)
class QolonyRootTask:
    task_id: str
    name: str
    task_type: str
    has_ecr: bool
    has_business_request: bool
    has_design_and_mass_production: bool


@dataclass(frozen=True)
class IquavisTaskSeed:
    source_sheet: str
    row_number: int
    iquavis_task_id: str
    name: str
    normalized_name: str
    target_project: str | None
    bucket_guess: str
    start_date: date | None
    end_date: date | None
    status: str | None
    progress_rate: int | None
    task_domain_id: str | None
    task_domain_row_number: int | None
    parent_task_id: str | None
    project_id: str | None
    assignee_login_id: str | None
    assignee_name: str | None


@dataclass(frozen=True)
class VisualItemSeed:
    source_sheet: str
    row_number: int
    column_number: int
    kind: str
    level: int | None
    path: str
    label_raw: str
    label_normalized: str
    visual_date: date | None
    target_project: str | None
    bucket_guess: str


@dataclass(frozen=True)
class VisualTaskCandidate:
    visual_item: VisualItemSeed
    task: IquavisTaskSeed
    score: int


@dataclass(frozen=True)
class SheetImageSeed:
    source_sheet: str
    image_index: int
    image_name: str
    from_cell: str
    to_cell: str
    extracted_path: Path


@dataclass(frozen=True)
class MappingSeedResult:
    root_tasks: list[QolonyRootTask]
    visual_sheet_names: list[str]
    visual_items: list[VisualItemSeed]
    sheet_images: list[SheetImageSeed]
    task_rows: list[IquavisTaskSeed]
    candidates: list[VisualTaskCandidate]
    output_path: Path


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _as_optional_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = _as_text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _as_optional_int(value: Any) -> int | None:
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


def normalize_matching_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", _as_text(text))
    normalized = normalized.replace("▽", "")
    normalized = normalized.replace("▼", "")
    normalized = normalized.replace("→", "")
    normalized = re.sub(r"[【】\[\]\(\)（）\s　'\"&/&、,，・･･_\-]+", "", normalized)
    return normalized.lower()


def guess_bucket(text: str) -> str:
    source = unicodedata.normalize("NFKC", _as_text(text))
    if "ECR" in source.upper():
        return "ECR"
    if "業務依頼" in source:
        return "業務依頼"
    return "設計検討・量産出図"


def guess_target_project(text: str, target_projects: list[str]) -> str | None:
    source = unicodedata.normalize("NFKC", _as_text(text))
    matched = [project for project in target_projects if project and project in source]
    if not matched:
        return None
    return sorted(matched, key=len, reverse=True)[0]


def _sheet_has_visible_gantt_layout(worksheet: Worksheet) -> bool:
    year = _as_optional_int(worksheet.cell(5, 4).value)
    month = _as_optional_int(worksheet.cell(6, 4).value)
    day = _as_optional_int(worksheet.cell(7, 4).value)
    if not year or not month or not day:
        return False
    for row_number in range(11, min(worksheet.max_row, 120) + 1):
        if _as_text(worksheet.cell(row_number, 2).value) or _as_text(worksheet.cell(row_number, 3).value):
            return True
    return False


def detect_visible_gantt_sheet_names(workbook_path: Path) -> list[str]:
    workbook = load_workbook(workbook_path, read_only=False, data_only=True, keep_vba=True)
    try:
        sheet_names: list[str] = []
        for worksheet in workbook.worksheets:
            if worksheet.title in EXCLUDED_VISUAL_SHEET_NAMES:
                continue
            if _sheet_has_visible_gantt_layout(worksheet):
                sheet_names.append(worksheet.title)
        return sheet_names
    finally:
        workbook.close()


def _worksheet_targets_by_name(workbook_path: Path) -> dict[str, str]:
    with zipfile.ZipFile(workbook_path) as archive:
        workbook_xml = ET.fromstring(archive.read("xl/workbook.xml"))
        rels_xml = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))

    ns = {
        "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "pkg": "http://schemas.openxmlformats.org/package/2006/relationships",
    }
    target_by_rid = {
        relation.attrib["Id"]: relation.attrib["Target"]
        for relation in rels_xml.findall("pkg:Relationship", ns)
        if relation.attrib.get("Type", "").endswith("/worksheet")
    }
    result: dict[str, str] = {}
    for sheet in workbook_xml.findall("main:sheets/main:sheet", ns):
        name = sheet.attrib.get("name")
        rid = sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        if name and rid and rid in target_by_rid:
            target = target_by_rid[rid].lstrip("/")
            if not target.startswith("xl/"):
                target = f"xl/{target}"
            result[name] = target
    return result


def _cell_ref_from_zero_based(col_index: int, row_index: int) -> str:
    return f"{get_column_letter(col_index + 1)}{row_index + 1}"


def extract_sheet_images(
    workbook_path: Path,
    *,
    sheet_names: list[str],
    assets_dir: Path,
) -> list[SheetImageSeed]:
    ns = {
        "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
        "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "pkg": "http://schemas.openxmlformats.org/package/2006/relationships",
    }
    worksheet_targets = _worksheet_targets_by_name(workbook_path)
    images: list[SheetImageSeed] = []
    assets_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(workbook_path) as archive:
        for sheet_name in sheet_names:
            worksheet_target = worksheet_targets.get(sheet_name)
            if not worksheet_target:
                continue
            rel_path = Path(worksheet_target)
            worksheet_rels = rel_path.parent / "_rels" / f"{rel_path.name}.rels"
            worksheet_rels_name = worksheet_rels.as_posix()
            if worksheet_rels_name not in archive.namelist():
                continue

            rels_xml = ET.fromstring(archive.read(worksheet_rels_name))
            drawing_target: str | None = None
            for relation in rels_xml.findall("pkg:Relationship", ns):
                if relation.attrib.get("Type", "").endswith("/drawing"):
                    drawing_target = relation.attrib.get("Target")
                    break
            if not drawing_target:
                continue

            drawing_path = posixpath.normpath((rel_path.parent / drawing_target).as_posix())
            drawing_rels_name = f"{Path(drawing_path).parent.as_posix()}/_rels/{Path(drawing_path).name}.rels"

            drawing_xml = ET.fromstring(archive.read(drawing_path))
            drawing_rels_xml = ET.fromstring(archive.read(drawing_rels_name))
            image_target_by_rid = {
                relation.attrib["Id"]: relation.attrib["Target"]
                for relation in drawing_rels_xml.findall("pkg:Relationship", ns)
                if relation.attrib.get("Type", "").endswith("/image")
            }

            image_index = 0
            for anchor in drawing_xml.findall("xdr:twoCellAnchor", ns):
                blip = anchor.find(".//a:blip", ns)
                if blip is None:
                    continue
                rid = blip.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
                target = image_target_by_rid.get(rid or "")
                if not target:
                    continue
                image_index += 1

                media_path = posixpath.normpath((Path(drawing_path).parent / target).as_posix())

                from_node = anchor.find("xdr:from", ns)
                to_node = anchor.find("xdr:to", ns)
                if from_node is None or to_node is None:
                    continue
                from_cell = _cell_ref_from_zero_based(
                    int(from_node.findtext("xdr:col", default="0", namespaces=ns)),
                    int(from_node.findtext("xdr:row", default="0", namespaces=ns)),
                )
                to_cell = _cell_ref_from_zero_based(
                    int(to_node.findtext("xdr:col", default="0", namespaces=ns)),
                    int(to_node.findtext("xdr:row", default="0", namespaces=ns)),
                )

                image_name = Path(media_path).name
                extracted_name = f"{sheet_name}_{image_index:03d}_{image_name}"
                extracted_path = assets_dir / extracted_name
                extracted_path.write_bytes(archive.read(media_path))
                images.append(
                    SheetImageSeed(
                        source_sheet=sheet_name,
                        image_index=image_index,
                        image_name=image_name,
                        from_cell=from_cell,
                        to_cell=to_cell,
                        extracted_path=extracted_path,
                    )
                )

    return images


def build_visible_gantt_date_map(worksheet: Worksheet) -> dict[int, date]:
    current_year: int | None = None
    current_month: int | None = None
    date_map: dict[int, date] = {}
    for column_number in range(4, worksheet.max_column + 1):
        year = _as_optional_int(worksheet.cell(5, column_number).value)
        month = _as_optional_int(worksheet.cell(6, column_number).value)
        day = _as_optional_int(worksheet.cell(7, column_number).value)
        if year:
            current_year = year
        if month:
            current_month = month
        if current_year and current_month and day:
            try:
                date_map[column_number] = date(current_year, current_month, day)
            except ValueError:
                continue
    return date_map


def _extract_domain_label(worksheet: Worksheet, row_number: int) -> tuple[int, str] | None:
    for column_number in (2, 3):
        text = _as_text(worksheet.cell(row_number, column_number).value)
        if not text:
            continue
        if text == "イベント日程":
            return None
        return column_number - 1, text
    return None


def _should_keep_visual_label(text: str) -> bool:
    cleaned = _as_text(text)
    if not cleaned:
        return False
    if cleaned in {"▽", "▼"}:
        return False
    if not normalize_matching_text(cleaned):
        return False
    return True


def extract_visual_items(
    workbook_path: Path,
    *,
    sheet_names: list[str],
    target_projects: list[str],
) -> list[VisualItemSeed]:
    workbook = load_workbook(workbook_path, read_only=False, data_only=True, keep_vba=True)
    try:
        items: list[VisualItemSeed] = []
        for sheet_name in sheet_names:
            worksheet = workbook[sheet_name]
            date_map = build_visible_gantt_date_map(worksheet)
            hierarchy: dict[int, str] = {}
            for row_number in range(11, worksheet.max_row + 1):
                domain = _extract_domain_label(worksheet, row_number)
                if domain is not None:
                    level, label = domain
                    hierarchy[level] = label
                    for key in list(hierarchy.keys()):
                        if key > level:
                            hierarchy.pop(key)
                    path = " / ".join(hierarchy[key] for key in sorted(hierarchy))
                    items.append(
                        VisualItemSeed(
                            source_sheet=sheet_name,
                            row_number=row_number,
                            column_number=2 if level == 1 else 3,
                            kind="task_domain",
                            level=level,
                            path=path,
                            label_raw=label,
                            label_normalized=normalize_matching_text(label),
                            visual_date=None,
                            target_project=guess_target_project(path, target_projects),
                            bucket_guess=guess_bucket(path),
                        )
                    )

                current_path = " / ".join(hierarchy[key] for key in sorted(hierarchy))
                if not current_path:
                    continue

                for column_number in range(4, worksheet.max_column + 1):
                    text = _as_text(worksheet.cell(row_number, column_number).value)
                    if not _should_keep_visual_label(text):
                        continue
                    items.append(
                        VisualItemSeed(
                            source_sheet=sheet_name,
                            row_number=row_number,
                            column_number=column_number,
                            kind="visual_task",
                            level=None,
                            path=current_path,
                            label_raw=text,
                            label_normalized=normalize_matching_text(text),
                            visual_date=date_map.get(column_number),
                            target_project=guess_target_project(f"{current_path} {text}", target_projects),
                            bucket_guess=guess_bucket(f"{current_path} {text}"),
                        )
                    )
        return items
    finally:
        workbook.close()


def _build_header_map(headers: list[Any]) -> dict[str, int]:
    header_map: dict[str, int] = {}
    for index, value in enumerate(headers):
        text = _as_text(value)
        if text:
            header_map[text] = index
    return header_map


def _parse_assigns(value: Any) -> tuple[str | None, str | None]:
    # 既存のAssignsはJSON文字列のため、軽量に文字列検索せずJSON化は避けずに行う。
    text = _as_text(value)
    if not text:
        return None, None
    try:
        import json

        payload = json.loads(text)
    except Exception:
        return None, None
    if not isinstance(payload, list) or not payload:
        return None, None
    first = payload[0]
    if not isinstance(first, dict):
        return None, None
    return _as_text(first.get("LoginId")) or None, _as_text(first.get("Name")) or None


def load_uncompleted_iquavis_tasks(
    workbook_path: Path,
    *,
    sheet_names: list[str],
    target_projects: list[str],
) -> list[IquavisTaskSeed]:
    workbook = load_workbook(workbook_path, read_only=False, data_only=True, keep_vba=True)
    try:
        tasks: list[IquavisTaskSeed] = []
        for sheet_name in sheet_names:
            worksheet = workbook[sheet_name]
            rows = worksheet.iter_rows(min_row=1, values_only=True)
            header_row = next(rows, None)
            if header_row is None:
                continue
            header_map = _build_header_map(list(header_row))
            for row_number, row in enumerate(rows, start=2):
                if not row:
                    continue
                name = _as_text(row[header_map["Name"]]) if "Name" in header_map else ""
                if not name:
                    continue
                status = _as_text(row[header_map["Progress.Status"]]) if "Progress.Status" in header_map else None
                if status == "Completion":
                    continue
                target_project = guess_target_project(name, target_projects)
                if target_project is None:
                    continue
                assigns = row[header_map["Assigns"]] if "Assigns" in header_map else None
                assignee_login_id, assignee_name = _parse_assigns(assigns)
                tasks.append(
                    IquavisTaskSeed(
                        source_sheet=sheet_name,
                        row_number=row_number,
                        iquavis_task_id=_as_text(row[header_map["Id"]]) if "Id" in header_map else "",
                        name=name,
                        normalized_name=normalize_matching_text(name),
                        target_project=target_project,
                        bucket_guess=guess_bucket(name),
                        start_date=_as_optional_date(row[header_map["StartDate"]]) if "StartDate" in header_map else None,
                        end_date=_as_optional_date(row[header_map["EndDate"]]) if "EndDate" in header_map else None,
                        status=status or None,
                        progress_rate=_as_optional_int(row[header_map["Progress.ProgressRate"]])
                        if "Progress.ProgressRate" in header_map
                        else None,
                        task_domain_id=_as_text(row[header_map["TaskDomainId"]]) or None
                        if "TaskDomainId" in header_map
                        else None,
                        task_domain_row_number=_as_optional_int(row[header_map["TaskDomainRowNumber"]])
                        if "TaskDomainRowNumber" in header_map
                        else None,
                        parent_task_id=_as_text(row[header_map["ParentTaskId"]]) or None
                        if "ParentTaskId" in header_map
                        else None,
                        project_id=_as_text(row[header_map["ProjectId"]]) or None
                        if "ProjectId" in header_map
                        else None,
                        assignee_login_id=assignee_login_id,
                        assignee_name=assignee_name,
                    )
                )
        return tasks
    finally:
        workbook.close()


def build_visual_task_candidates(
    visual_items: list[VisualItemSeed],
    task_rows: list[IquavisTaskSeed],
) -> list[VisualTaskCandidate]:
    candidates: list[VisualTaskCandidate] = []
    for item in visual_items:
        if item.kind != "visual_task":
            continue
        if len(item.label_normalized) < 3:
            continue
        for task in task_rows:
            score = 0
            if item.target_project and task.target_project == item.target_project:
                score += 2
            if item.bucket_guess == task.bucket_guess:
                score += 1
            if item.label_normalized in task.normalized_name or task.normalized_name in item.label_normalized:
                score += 6
            if item.visual_date and task.end_date:
                day_gap = abs((task.end_date - item.visual_date).days)
                if day_gap <= 14:
                    score += 2
                elif day_gap <= 30:
                    score += 1
            if score > 0:
                candidates.append(VisualTaskCandidate(visual_item=item, task=task, score=score))
    candidates.sort(
        key=lambda item: (
            item.visual_item.source_sheet,
            item.visual_item.row_number,
            item.visual_item.column_number,
            -item.score,
            item.task.end_date or date.max,
            item.task.name,
        )
    )
    return candidates


def load_qolony_root_tasks(db_path: Path) -> list[QolonyRootTask]:
    connection = sqlite3.connect(db_path)
    try:
        cursor = connection.cursor()
        rows = cursor.execute(
            """
            SELECT parent.Id, parent.Name, parent.Type, child.Name
            FROM Tasks parent
            LEFT JOIN Tasks child ON child.ParentTaskId = parent.Id
            WHERE parent.ParentTaskId IS NULL
            ORDER BY parent.Name, child.Name
            """
        ).fetchall()
    finally:
        connection.close()

    grouped: dict[str, dict[str, Any]] = {}
    for task_id, name, task_type, child_name in rows:
        entry = grouped.setdefault(
            task_id,
            {
                "task_id": task_id,
                "name": name,
                "task_type": task_type,
                "children": set(),
            },
        )
        if child_name:
            entry["children"].add(child_name)

    result: list[QolonyRootTask] = []
    for entry in grouped.values():
        children = entry["children"]
        result.append(
            QolonyRootTask(
                task_id=entry["task_id"],
                name=entry["name"],
                task_type=entry["task_type"],
                has_ecr="ECR" in children,
                has_business_request="業務依頼" in children,
                has_design_and_mass_production="設計検討・量産出図" in children,
            )
        )
    result.sort(key=lambda item: item.name)
    return result


def _best_candidate_map(candidates: list[VisualTaskCandidate]) -> dict[tuple[str, int, int], VisualTaskCandidate]:
    best: dict[tuple[str, int, int], VisualTaskCandidate] = {}
    for candidate in candidates:
        if candidate.score < 6:
            continue
        key = (candidate.visual_item.source_sheet, candidate.visual_item.row_number, candidate.visual_item.column_number)
        if key not in best:
            best[key] = candidate
    return best


def _candidate_count_map(candidates: list[VisualTaskCandidate]) -> dict[tuple[str, int, int], int]:
    counts: dict[tuple[str, int, int], int] = {}
    for candidate in candidates:
        key = (candidate.visual_item.source_sheet, candidate.visual_item.row_number, candidate.visual_item.column_number)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _append_header(worksheet: Worksheet, headers: list[str]) -> None:
    worksheet.append(headers)
    for cell in worksheet[1]:
        cell.font = Font(bold=True)
    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = f"A1:{get_column_letter(len(headers))}1"


def _apply_column_widths(worksheet: Worksheet, widths: dict[str, float]) -> None:
    for column_letter, width in widths.items():
        worksheet.column_dimensions[column_letter].width = width


def write_mapping_seed_workbook(result: MappingSeedResult) -> None:
    workbook = Workbook()
    readme = workbook.active
    readme.title = "使い方"
    readme["A1"] = "このファイルは iQUAVIS 見た目ガントと未完了タスクの初期マッピング用です。"
    readme["A2"] = "見た目ガントは、セル文字があるシートは直接読んでいます。画像シートは画像ファイルを抽出しています。"
    readme["A3"] = "source_kind は task_domain / task / qolony_only を使ってください。"
    readme["A4"] = "target_bucket は ECR / 業務依頼 / 設計検討・量産出図 を想定しています。"
    readme["A5"] = "見た目ガントの候補紐付けは自動推定なので、必ず人手で確認してください。"
    readme["A6"] = f"対象の見た目ガントシート: {', '.join(result.visual_sheet_names) if result.visual_sheet_names else 'なし'}"
    readme["A7"] = "Sheet3 のような画像シートは、この環境では OpenAI 側OCRへ直接送れないため、画像抽出まで行っています。"
    readme.column_dimensions["A"].width = 120

    projects_sheet = workbook.create_sheet("QOLONY案件")
    _append_header(
        projects_sheet,
        [
            "root_task_id",
            "root_task_name",
            "root_task_type",
            "has_ECR",
            "has_業務依頼",
            "has_設計検討・量産出図",
        ],
    )
    for task in result.root_tasks:
        projects_sheet.append(
            [
                task.task_id,
                task.name,
                task.task_type,
                "Y" if task.has_ecr else "",
                "Y" if task.has_business_request else "",
                "Y" if task.has_design_and_mass_production else "",
            ]
        )
    _apply_column_widths(projects_sheet, {"A": 38, "B": 20, "C": 14, "D": 10, "E": 12, "F": 18})

    tasks_sheet = workbook.create_sheet("iQUAVIS未完了タスク")
    _append_header(
        tasks_sheet,
        [
            "source_sheet",
            "row_number",
            "iquavis_task_id",
            "name",
            "target_project",
            "bucket_guess",
            "start_date",
            "end_date",
            "status",
            "progress_rate",
            "task_domain_id",
            "task_domain_row_number",
            "parent_task_id",
            "project_id",
            "assignee_login_id",
            "assignee_name",
        ],
    )
    for task in result.task_rows:
        tasks_sheet.append(
            [
                task.source_sheet,
                task.row_number,
                task.iquavis_task_id,
                task.name,
                task.target_project,
                task.bucket_guess,
                task.start_date,
                task.end_date,
                task.status,
                task.progress_rate,
                task.task_domain_id,
                task.task_domain_row_number,
                task.parent_task_id,
                task.project_id,
                task.assignee_login_id,
                task.assignee_name,
            ]
        )
    _apply_column_widths(
        tasks_sheet,
        {
            "A": 24,
            "B": 10,
            "C": 38,
            "D": 90,
            "E": 16,
            "F": 18,
            "G": 14,
            "H": 14,
            "I": 14,
            "J": 12,
            "K": 38,
            "L": 12,
            "M": 38,
            "N": 38,
            "O": 16,
            "P": 16,
        },
    )

    image_sheet = workbook.create_sheet("見た目画像一覧")
    _append_header(
        image_sheet,
        [
            "source_sheet",
            "image_index",
            "image_name",
            "from_cell",
            "to_cell",
            "extracted_path",
        ],
    )
    for item in result.sheet_images:
        image_sheet.append(
            [
                item.source_sheet,
                item.image_index,
                item.image_name,
                item.from_cell,
                item.to_cell,
                str(item.extracted_path),
            ]
        )
    _apply_column_widths(image_sheet, {"A": 20, "B": 12, "C": 20, "D": 12, "E": 12, "F": 80})

    best_candidate_by_item = _best_candidate_map(result.candidates)
    candidate_count_by_item = _candidate_count_map(result.candidates)

    visual_sheet = workbook.create_sheet("見た目ガント読取")
    _append_header(
        visual_sheet,
        [
            "source_sheet",
            "row_number",
            "column",
            "kind",
            "level",
            "path",
            "label_raw",
            "visual_date",
            "target_project_guess",
            "bucket_guess",
            "best_task_name",
            "best_task_id",
            "best_task_due",
            "best_task_domain_id",
            "best_task_domain_row_number",
            "candidate_count",
        ],
    )
    for item in result.visual_items:
        key = (item.source_sheet, item.row_number, item.column_number)
        candidate = best_candidate_by_item.get(key)
        visual_sheet.append(
            [
                item.source_sheet,
                item.row_number,
                get_column_letter(item.column_number),
                item.kind,
                item.level,
                item.path,
                item.label_raw,
                item.visual_date,
                item.target_project,
                item.bucket_guess,
                candidate.task.name if candidate else None,
                candidate.task.iquavis_task_id if candidate else None,
                candidate.task.end_date if candidate else None,
                candidate.task.task_domain_id if candidate else None,
                candidate.task.task_domain_row_number if candidate else None,
                candidate_count_by_item.get(key, 0),
            ]
        )
    _apply_column_widths(
        visual_sheet,
        {
            "A": 28,
            "B": 10,
            "C": 8,
            "D": 14,
            "E": 8,
            "F": 50,
            "G": 50,
            "H": 14,
            "I": 16,
            "J": 18,
            "K": 48,
            "L": 38,
            "M": 14,
            "N": 38,
            "O": 12,
            "P": 14,
        },
    )

    candidate_sheet = workbook.create_sheet("候補紐付け")
    _append_header(
        candidate_sheet,
        [
            "source_sheet",
            "row_number",
            "column",
            "visual_label",
            "visual_path",
            "visual_date",
            "candidate_rank",
            "score",
            "iquavis_task_id",
            "iquavis_task_name",
            "iquavis_end_date",
            "target_project",
            "bucket_guess",
            "task_domain_id",
            "task_domain_row_number",
        ],
    )
    grouped_candidates: dict[tuple[str, int, int], list[VisualTaskCandidate]] = {}
    for candidate in result.candidates:
        key = (candidate.visual_item.source_sheet, candidate.visual_item.row_number, candidate.visual_item.column_number)
        grouped_candidates.setdefault(key, []).append(candidate)
    for key, candidates in grouped_candidates.items():
        for rank, candidate in enumerate(candidates[:5], start=1):
            candidate_sheet.append(
                [
                    candidate.visual_item.source_sheet,
                    candidate.visual_item.row_number,
                    get_column_letter(candidate.visual_item.column_number),
                    candidate.visual_item.label_raw,
                    candidate.visual_item.path,
                    candidate.visual_item.visual_date,
                    rank,
                    candidate.score,
                    candidate.task.iquavis_task_id,
                    candidate.task.name,
                    candidate.task.end_date,
                    candidate.task.target_project,
                    candidate.task.bucket_guess,
                    candidate.task.task_domain_id,
                    candidate.task.task_domain_row_number,
                ]
            )
    _apply_column_widths(
        candidate_sheet,
        {
            "A": 28,
            "B": 10,
            "C": 8,
            "D": 40,
            "E": 48,
            "F": 14,
            "G": 12,
            "H": 8,
            "I": 38,
            "J": 90,
            "K": 14,
            "L": 16,
            "M": 18,
            "N": 38,
            "O": 12,
        },
    )

    mapping_sheet = workbook.create_sheet("マッピング")
    _append_header(
        mapping_sheet,
        [
            "use",
            "source_kind",
            "visual_sheet",
            "visual_row_or_image_index",
            "visual_label_or_image_name",
            "visual_path_or_anchor",
            "visual_date",
            "source_image_path",
            "source_project_guess",
            "source_bucket_guess",
            "iquavis_task_domain_id",
            "iquavis_task_id",
            "iquavis_task_name",
            "iquavis_end_date",
            "target_top_project",
            "target_bucket",
            "target_parent_qolony_task_id",
            "target_display_name",
            "note",
        ],
    )
    for item in result.visual_items:
        key = (item.source_sheet, item.row_number, item.column_number)
        candidate = best_candidate_by_item.get(key)
        mapping_sheet.append(
            [
                "",
                "task_domain" if item.kind == "task_domain" else "task",
                item.source_sheet,
                item.row_number,
                item.label_raw,
                item.path,
                item.visual_date,
                "",
                item.target_project,
                item.bucket_guess,
                candidate.task.task_domain_id if candidate else "",
                candidate.task.iquavis_task_id if candidate else "",
                candidate.task.name if candidate else "",
                candidate.task.end_date if candidate else "",
                item.target_project or "",
                item.bucket_guess,
                "",
                candidate.task.name if candidate else item.label_raw,
                "",
            ]
        )
    for item in result.sheet_images:
        mapping_sheet.append(
            [
                "",
                "sheet_image",
                item.source_sheet,
                item.image_index,
                item.image_name,
                f"{item.from_cell}:{item.to_cell}",
                "",
                str(item.extracted_path),
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
            ]
        )
    _apply_column_widths(
        mapping_sheet,
        {
            "A": 8,
            "B": 14,
            "C": 28,
            "D": 16,
            "E": 36,
            "F": 48,
            "G": 14,
            "H": 80,
            "I": 16,
            "J": 18,
            "K": 38,
            "L": 38,
            "M": 90,
            "N": 14,
            "O": 18,
            "P": 18,
            "Q": 38,
            "R": 42,
            "S": 36,
        },
    )

    result.output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(result.output_path)


def generate_mapping_seed_workbook(
    *,
    workbook_path: Path,
    db_path: Path,
    output_path: Path,
    task_sheet_names: list[str] | None = None,
    visual_sheet_names: list[str] | None = None,
) -> MappingSeedResult:
    root_tasks = load_qolony_root_tasks(db_path)
    target_projects = [task.name for task in root_tasks]
    selected_task_sheet_names = task_sheet_names or DEFAULT_TASK_SHEET_NAMES
    selected_visual_sheet_names = visual_sheet_names or detect_visible_gantt_sheet_names(workbook_path)
    assets_dir = output_path.with_suffix("")
    assets_dir = assets_dir.parent / f"{assets_dir.name}_assets"
    task_rows = load_uncompleted_iquavis_tasks(
        workbook_path,
        sheet_names=selected_task_sheet_names,
        target_projects=target_projects,
    )
    visual_items = extract_visual_items(
        workbook_path,
        sheet_names=selected_visual_sheet_names,
        target_projects=target_projects,
    )
    sheet_images = extract_sheet_images(
        workbook_path,
        sheet_names=selected_visual_sheet_names,
        assets_dir=assets_dir,
    )
    candidates = build_visual_task_candidates(visual_items, task_rows)
    result = MappingSeedResult(
        root_tasks=root_tasks,
        visual_sheet_names=selected_visual_sheet_names,
        visual_items=visual_items,
        sheet_images=sheet_images,
        task_rows=task_rows,
        candidates=candidates,
        output_path=output_path,
    )
    write_mapping_seed_workbook(result)
    return result
