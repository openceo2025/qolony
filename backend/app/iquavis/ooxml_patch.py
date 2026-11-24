from __future__ import annotations

import math
import os
import posixpath
import re
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from xml.etree import ElementTree
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


_OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_CELL_REF_RE = re.compile(r"^([A-Z]+)([1-9][0-9]*)$", re.IGNORECASE)
_TAG_PREFIX = r"(?:[A-Za-z_][\w.-]*:)?"


class OoxmlPatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class OoxmlCellPatch:
    sheet_name: str
    coordinate: str
    value: Any
    yellow_fill: bool = False
    clear_yellow_fill: bool = False


@dataclass(frozen=True)
class _IndexedCell:
    coordinate: str
    start: int
    end: int
    xml: str
    style_id: int
    column_number: int


@dataclass(frozen=True)
class _IndexedRow:
    row_number: int
    start: int
    end: int
    xml: str
    close_start: int
    prefix: str
    cells: tuple[_IndexedCell, ...]


@dataclass(frozen=True)
class _WorksheetIndex:
    rows_by_number: dict[int, _IndexedRow]
    cells_by_coordinate: dict[str, _IndexedCell]


def patch_ooxml_cells(workbook_path: Path, patches: list[OoxmlCellPatch]) -> None:
    """対象セルと必要なスタイルだけを更新し、それ以外のOOXML部品を保持する。"""

    if not patches:
        return

    deduplicated: dict[tuple[str, str], OoxmlCellPatch] = {}
    for patch in patches:
        coordinate = patch.coordinate.upper()
        if _CELL_REF_RE.fullmatch(coordinate) is None:
            raise OoxmlPatchError(f"invalid cell coordinate: {patch.coordinate}")
        deduplicated[(patch.sheet_name, coordinate)] = OoxmlCellPatch(
            sheet_name=patch.sheet_name,
            coordinate=coordinate,
            value=patch.value,
            yellow_fill=patch.yellow_fill,
            clear_yellow_fill=patch.clear_yellow_fill,
        )

    with ZipFile(workbook_path, "r") as source:
        entries = [(info, source.read(info)) for info in source.infolist()]
        archive_comment = source.comment

    parts = {info.filename: payload for info, payload in entries}
    workbook_xml = parts.get("xl/workbook.xml")
    workbook_rels_xml = parts.get("xl/_rels/workbook.xml.rels")
    if workbook_xml is None or workbook_rels_xml is None:
        raise OoxmlPatchError("workbook.xml or its relationships are missing")

    sheet_parts, styles_part = _workbook_part_map(workbook_xml, workbook_rels_xml)
    grouped: dict[str, list[OoxmlCellPatch]] = {}
    for patch in deduplicated.values():
        part_name = sheet_parts.get(patch.sheet_name)
        if part_name is None or part_name not in parts:
            raise OoxmlPatchError(f"worksheet part not found: {patch.sheet_name}")
        grouped.setdefault(part_name, []).append(patch)

    sheet_texts = {part_name: _decode_xml(parts[part_name]) for part_name in grouped}
    sheet_indexes = {
        part_name: _index_worksheet(sheet_text)
        for part_name, sheet_text in sheet_texts.items()
    }
    requested_style_ids: set[int] = set()
    requested_clear_style_ids: set[int] = set()
    for part_name, part_patches in grouped.items():
        worksheet_index = sheet_indexes[part_name]
        for patch in part_patches:
            if patch.yellow_fill:
                indexed_cell = worksheet_index.cells_by_coordinate.get(patch.coordinate)
                requested_style_ids.add(indexed_cell.style_id if indexed_cell is not None else 0)
            if patch.clear_yellow_fill:
                indexed_cell = worksheet_index.cells_by_coordinate.get(patch.coordinate)
                requested_clear_style_ids.add(indexed_cell.style_id if indexed_cell is not None else 0)

    modifications: dict[str, bytes] = {}
    yellow_style_by_source: dict[int, int] = {}
    non_yellow_style_by_source: dict[int, int] = {}
    if requested_style_ids or requested_clear_style_ids:
        if styles_part is None or styles_part not in parts:
            raise OoxmlPatchError("styles.xml is missing")
        styles_text = _decode_xml(parts[styles_part])
        if requested_style_ids:
            styles_text, yellow_style_by_source = _ensure_yellow_styles(styles_text, requested_style_ids)
        if requested_clear_style_ids:
            styles_text, non_yellow_style_by_source = _ensure_non_yellow_styles(
                styles_text,
                requested_clear_style_ids,
            )
        modifications[styles_part] = _encode_xml(styles_text, parts[styles_part])

    date_1904 = _uses_1904_date_system(workbook_xml)
    for part_name, part_patches in grouped.items():
        sheet_text = _patch_indexed_worksheet(
            sheet_texts[part_name],
            part_patches,
            worksheet_index=sheet_indexes[part_name],
            yellow_style_by_source=yellow_style_by_source,
            non_yellow_style_by_source=non_yellow_style_by_source,
            date_1904=date_1904,
        )
        modifications[part_name] = _encode_xml(sheet_text, parts[part_name])

    _rewrite_archive(workbook_path, entries, archive_comment, modifications)


def _workbook_part_map(workbook_xml: bytes, workbook_rels_xml: bytes) -> tuple[dict[str, str], str | None]:
    try:
        workbook_root = ElementTree.fromstring(workbook_xml)
        rels_root = ElementTree.fromstring(workbook_rels_xml)
    except ElementTree.ParseError as exc:
        raise OoxmlPatchError("workbook relationships could not be parsed") from exc

    relationships: dict[str, tuple[str, str]] = {}
    styles_part: str | None = None
    for relationship in rels_root:
        if _local_name(relationship.tag) != "Relationship":
            continue
        rel_id = relationship.attrib.get("Id")
        target = relationship.attrib.get("Target")
        rel_type = relationship.attrib.get("Type", "")
        if not rel_id or not target or relationship.attrib.get("TargetMode") == "External":
            continue
        target_part = _resolve_part_target("xl/workbook.xml", target)
        relationships[rel_id] = (rel_type, target_part)
        if rel_type.endswith("/styles"):
            styles_part = target_part

    sheet_parts: dict[str, str] = {}
    for element in workbook_root.iter():
        if _local_name(element.tag) != "sheet":
            continue
        sheet_name = element.attrib.get("name")
        rel_id = element.attrib.get(f"{{{_OFFICE_REL_NS}}}id")
        if rel_id is None:
            rel_id = next((value for key, value in element.attrib.items() if _local_name(key) == "id"), None)
        relationship = relationships.get(rel_id or "")
        if sheet_name and relationship and relationship[0].endswith("/worksheet"):
            sheet_parts[sheet_name] = relationship[1]
    return sheet_parts, styles_part


def _resolve_part_target(source_part: str, target: str) -> str:
    normalized_target = target.replace("\\", "/")
    if normalized_target.startswith("/"):
        return posixpath.normpath(normalized_target.lstrip("/"))
    return posixpath.normpath(posixpath.join(posixpath.dirname(source_part), normalized_target))


def _decode_xml(payload: bytes) -> str:
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise OoxmlPatchError("OOXML XML must be UTF-8") from exc


def _encode_xml(text: str, original: bytes) -> bytes:
    encoded = text.encode("utf-8")
    if original.startswith(b"\xef\xbb\xbf"):
        return b"\xef\xbb\xbf" + encoded
    return encoded


def _uses_1904_date_system(workbook_xml: bytes) -> bool:
    text = _decode_xml(workbook_xml)
    match = re.search(rf"<{_TAG_PREFIX}workbookPr\b[^>]*>", text, re.IGNORECASE)
    if match is None:
        match = re.search(rf"<{_TAG_PREFIX}workbookPr\b[^>]*/\s*>", text, re.IGNORECASE)
    if match is None:
        return False
    return (_attribute_value(match.group(0), "date1904") or "").lower() in {"1", "true"}


def _index_worksheet(sheet_text: str) -> _WorksheetIndex:
    """ワークシートを1回走査し、行とセルの位置を再利用できる形で保持する。"""

    row_pattern = re.compile(
        rf"<{_TAG_PREFIX}row\b[^>]*>.*?</{_TAG_PREFIX}row\s*>",
        re.IGNORECASE | re.DOTALL,
    )
    cell_pattern = re.compile(
        rf"(?:"
        rf"<{_TAG_PREFIX}c\b[^>]*?/\s*>"
        rf"|<{_TAG_PREFIX}c\b[^>]*>.*?</{_TAG_PREFIX}c\s*>"
        rf")",
        re.IGNORECASE | re.DOTALL,
    )
    rows_by_number: dict[int, _IndexedRow] = {}
    cells_by_coordinate: dict[str, _IndexedCell] = {}

    for row_match in row_pattern.finditer(sheet_text):
        row_xml = row_match.group(0)
        row_open_end = row_xml.find(">") + 1
        row_open_tag = row_xml[:row_open_end]
        row_number_text = _attribute_value(row_open_tag, "r")
        try:
            row_number = int(row_number_text or "")
        except ValueError:
            continue
        if row_number <= 0:
            continue

        row_prefix_match = re.match(r"<(?P<prefix>[A-Za-z_][\w.-]*:)?row\b", row_xml, re.IGNORECASE)
        row_prefix = row_prefix_match.group("prefix") if row_prefix_match else ""
        indexed_cells: list[_IndexedCell] = []
        for cell_match in cell_pattern.finditer(row_xml):
            cell_xml = cell_match.group(0)
            cell_open_end = cell_xml.find(">") + 1
            coordinate = (_attribute_value(cell_xml[:cell_open_end], "r") or "").upper()
            if _CELL_REF_RE.fullmatch(coordinate) is None:
                continue
            column, _ = _coordinate_parts(coordinate)
            style_text = _attribute_value(cell_xml[:cell_open_end], "s")
            try:
                style_id = int(style_text) if style_text is not None else 0
            except ValueError:
                style_id = 0
            indexed_cell = _IndexedCell(
                coordinate=coordinate,
                start=cell_match.start(),
                end=cell_match.end(),
                xml=cell_xml,
                style_id=style_id,
                column_number=_column_number(column),
            )
            indexed_cells.append(indexed_cell)
            cells_by_coordinate[coordinate] = indexed_cell

        indexed_row = _IndexedRow(
            row_number=row_number,
            start=row_match.start(),
            end=row_match.end(),
            xml=row_xml,
            close_start=row_xml.rfind("<"),
            prefix=row_prefix or "",
            cells=tuple(indexed_cells),
        )
        rows_by_number[row_number] = indexed_row

    return _WorksheetIndex(
        rows_by_number=rows_by_number,
        cells_by_coordinate=cells_by_coordinate,
    )


def _patch_indexed_worksheet(
    sheet_text: str,
    patches: list[OoxmlCellPatch],
    *,
    worksheet_index: _WorksheetIndex,
    yellow_style_by_source: dict[int, int],
    non_yellow_style_by_source: dict[int, int],
    date_1904: bool,
) -> str:
    patches_by_row: dict[int, list[OoxmlCellPatch]] = {}
    for patch in patches:
        _, row_number = _coordinate_parts(patch.coordinate)
        if row_number not in worksheet_index.rows_by_number:
            raise OoxmlPatchError(f"worksheet row not found: {patch.coordinate}")
        patches_by_row.setdefault(row_number, []).append(patch)

    row_replacements: list[tuple[int, int, str]] = []
    for row_number, row_patches in patches_by_row.items():
        indexed_row = worksheet_index.rows_by_number[row_number]
        row_replacements.append(
            (
                indexed_row.start,
                indexed_row.end,
                _patch_indexed_row(
                    indexed_row,
                    row_patches,
                    yellow_style_by_source=yellow_style_by_source,
                    non_yellow_style_by_source=non_yellow_style_by_source,
                    date_1904=date_1904,
                ),
            )
        )

    chunks: list[str] = []
    cursor = 0
    for start, end, replacement in sorted(row_replacements):
        chunks.append(sheet_text[cursor:start])
        chunks.append(replacement)
        cursor = end
    chunks.append(sheet_text[cursor:])
    return "".join(chunks)


def _patch_indexed_row(
    indexed_row: _IndexedRow,
    patches: list[OoxmlCellPatch],
    *,
    yellow_style_by_source: dict[int, int],
    non_yellow_style_by_source: dict[int, int],
    date_1904: bool,
) -> str:
    cell_by_coordinate = {cell.coordinate: cell for cell in indexed_row.cells}
    replacements: dict[str, str] = {}
    insertions: dict[int, list[tuple[int, str]]] = {}

    for patch in patches:
        column, _ = _coordinate_parts(patch.coordinate)
        column_number = _column_number(column)
        indexed_cell = cell_by_coordinate.get(patch.coordinate)
        source_style_id = indexed_cell.style_id if indexed_cell is not None else 0
        if patch.yellow_fill:
            style_id = yellow_style_by_source[source_style_id]
        elif patch.clear_yellow_fill:
            style_id = non_yellow_style_by_source[source_style_id]
        else:
            style_id = None

        if indexed_cell is not None:
            replacements[patch.coordinate] = _replace_cell_element(
                indexed_cell.xml,
                patch.coordinate,
                patch.value,
                style_id=style_id,
                date_1904=date_1904,
            )
            continue

        insertion_offset = indexed_row.close_start
        for existing_cell in indexed_row.cells:
            if existing_cell.column_number > column_number:
                insertion_offset = existing_cell.start
                break
        insertions.setdefault(insertion_offset, []).append(
            (
                column_number,
                _new_cell_element(
                    patch.coordinate,
                    patch.value,
                    style_id=style_id,
                    date_1904=date_1904,
                    prefix=indexed_row.prefix,
                ),
            )
        )

    chunks: list[str] = []
    cursor = 0
    for indexed_cell in indexed_row.cells:
        chunks.append(indexed_row.xml[cursor:indexed_cell.start])
        chunks.extend(fragment for _, fragment in sorted(insertions.get(indexed_cell.start, [])))
        chunks.append(replacements.get(indexed_cell.coordinate, indexed_cell.xml))
        cursor = indexed_cell.end
    chunks.append(indexed_row.xml[cursor:indexed_row.close_start])
    chunks.extend(fragment for _, fragment in sorted(insertions.get(indexed_row.close_start, [])))
    chunks.append(indexed_row.xml[indexed_row.close_start:])
    return "".join(chunks)


def _cell_pattern(coordinate: str) -> re.Pattern[str]:
    escaped_coordinate = re.escape(coordinate)
    return re.compile(
        rf"(?:"
        rf"<{_TAG_PREFIX}c\b(?=[^>]*\br\s*=\s*['\"]{escaped_coordinate}['\"])[^>]*?/\s*>"
        rf"|<{_TAG_PREFIX}c\b(?=[^>]*\br\s*=\s*['\"]{escaped_coordinate}['\"])[^>]*>"
        rf".*?</{_TAG_PREFIX}c\s*>"
        rf")",
        re.IGNORECASE | re.DOTALL,
    )


def _cell_style_id(sheet_text: str, coordinate: str) -> int:
    match = _cell_pattern(coordinate).search(sheet_text)
    if match is None:
        return 0
    open_tag = match.group(0)[: match.group(0).find(">") + 1]
    value = _attribute_value(open_tag, "s")
    try:
        return int(value) if value is not None else 0
    except ValueError:
        return 0


def _patch_sheet_cell(
    sheet_text: str,
    coordinate: str,
    value: Any,
    *,
    style_id: int | None,
    date_1904: bool,
) -> str:
    match = _cell_pattern(coordinate).search(sheet_text)
    if match is not None:
        replacement = _replace_cell_element(
            match.group(0),
            coordinate,
            value,
            style_id=style_id,
            date_1904=date_1904,
        )
        return sheet_text[: match.start()] + replacement + sheet_text[match.end() :]
    return _insert_cell_element(
        sheet_text,
        coordinate,
        value,
        style_id=style_id,
        date_1904=date_1904,
    )


def _replace_cell_element(
    cell_xml: str,
    coordinate: str,
    value: Any,
    *,
    style_id: int | None,
    date_1904: bool,
) -> str:
    open_end = cell_xml.find(">") + 1
    open_tag = cell_xml[:open_end]
    prefix_match = re.match(r"<(?P<prefix>[A-Za-z_][\w.-]*:)?c\b", open_tag, re.IGNORECASE)
    prefix = prefix_match.group("prefix") if prefix_match else ""
    self_closing = open_tag.rstrip().endswith("/>")

    if style_id is not None:
        open_tag = _set_attribute(open_tag, "s", str(style_id))
    type_name, payload = _cell_value_payload(value, date_1904=date_1904, prefix=prefix or "")
    if type_name is None:
        open_tag = _remove_attribute(open_tag, "t")
    else:
        open_tag = _set_attribute(open_tag, "t", type_name)

    existing_inner = "" if self_closing else cell_xml[open_end : cell_xml.rfind("<")]
    remaining_inner = _remove_value_children(existing_inner)
    inner = payload + remaining_inner
    open_tag = re.sub(r"/\s*>$", ">", open_tag)
    if not inner:
        return re.sub(r">$", "/>", open_tag)
    return f"{open_tag}{inner}</{prefix or ''}c>"


def _insert_cell_element(
    sheet_text: str,
    coordinate: str,
    value: Any,
    *,
    style_id: int | None,
    date_1904: bool,
) -> str:
    target_column, target_row = _coordinate_parts(coordinate)
    row_pattern = re.compile(
        rf"<{_TAG_PREFIX}row\b(?=[^>]*\br\s*=\s*['\"]{target_row}['\"])[^>]*>.*?</{_TAG_PREFIX}row\s*>",
        re.IGNORECASE | re.DOTALL,
    )
    row_match = row_pattern.search(sheet_text)
    if row_match is None:
        raise OoxmlPatchError(f"worksheet row not found: {coordinate}")

    row_xml = row_match.group(0)
    row_open_end = row_xml.find(">") + 1
    row_close_start = row_xml.rfind("<")
    row_inner = row_xml[row_open_end:row_close_start]
    row_prefix_match = re.match(r"<(?P<prefix>[A-Za-z_][\w.-]*:)?row\b", row_xml, re.IGNORECASE)
    row_prefix = row_prefix_match.group("prefix") if row_prefix_match else ""
    cell_xml = _new_cell_element(
        coordinate,
        value,
        style_id=style_id,
        date_1904=date_1904,
        prefix=row_prefix or "",
    )

    insertion_offset = len(row_inner)
    any_cell_pattern = re.compile(
        rf"<{_TAG_PREFIX}c\b[^>]*\br\s*=\s*['\"](?P<coordinate>[A-Z]+[1-9][0-9]*)['\"][^>]*",
        re.IGNORECASE,
    )
    target_column_number = _column_number(target_column)
    for existing in any_cell_pattern.finditer(row_inner):
        existing_column, _ = _coordinate_parts(existing.group("coordinate"))
        if _column_number(existing_column) > target_column_number:
            insertion_offset = existing.start()
            break

    new_inner = row_inner[:insertion_offset] + cell_xml + row_inner[insertion_offset:]
    new_row_xml = row_xml[:row_open_end] + new_inner + row_xml[row_close_start:]
    return sheet_text[: row_match.start()] + new_row_xml + sheet_text[row_match.end() :]


def _new_cell_element(
    coordinate: str,
    value: Any,
    *,
    style_id: int | None,
    date_1904: bool,
    prefix: str,
) -> str:
    open_tag = f'<{prefix}c r="{coordinate}">'
    if style_id is not None:
        open_tag = _set_attribute(open_tag, "s", str(style_id))
    type_name, payload = _cell_value_payload(value, date_1904=date_1904, prefix=prefix)
    if type_name is not None:
        open_tag = _set_attribute(open_tag, "t", type_name)
    if not payload:
        return re.sub(r">$", "/>", open_tag)
    return f"{open_tag}{payload}</{prefix}c>"


def _cell_value_payload(value: Any, *, date_1904: bool, prefix: str) -> tuple[str | None, str]:
    tag = lambda name: f"{prefix}{name}"
    if value is None or value == "":
        return None, ""
    if isinstance(value, bool):
        return "b", f"<{tag('v')}>{1 if value else 0}</{tag('v')}>"
    if isinstance(value, datetime):
        serial = _excel_serial(value, date_1904=date_1904)
        return None, f"<{tag('v')}>{_number_text(serial)}</{tag('v')}>"
    if isinstance(value, date):
        serial = _excel_serial(datetime.combine(value, datetime.min.time()), date_1904=date_1904)
        return None, f"<{tag('v')}>{_number_text(serial)}</{tag('v')}>"
    if isinstance(value, (int, float, Decimal)):
        return None, f"<{tag('v')}>{_number_text(value)}</{tag('v')}>"

    text = str(value)
    preserve = text != text.strip() or any(character in text for character in "\r\n\t")
    space_attribute = ' xml:space="preserve"' if preserve else ""
    escaped_text = escape(text)
    return "inlineStr", f"<{tag('is')}><{tag('t')}{space_attribute}>{escaped_text}</{tag('t')}></{tag('is')}>"


def _excel_serial(value: datetime, *, date_1904: bool) -> float:
    if date_1904:
        epoch = datetime(1904, 1, 1)
        return (value - epoch).total_seconds() / 86400
    epoch = datetime(1899, 12, 30)
    serial = (value - epoch).total_seconds() / 86400
    if 0 < serial <= 60:
        serial -= 1
    return serial


def _number_text(value: int | float | Decimal) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, int):
        return str(value)
    if not math.isfinite(value):
        raise OoxmlPatchError("non-finite numeric values cannot be written to Excel")
    return format(value, ".15g")


def _remove_value_children(inner_xml: str) -> str:
    result = inner_xml
    for local_name in ("f", "v", "is"):
        result = re.sub(
            rf"(?:<{_TAG_PREFIX}{local_name}\b[^>]*?/\s*>"
            rf"|<{_TAG_PREFIX}{local_name}\b[^>]*>.*?</{_TAG_PREFIX}{local_name}\s*>)",
            "",
            result,
            flags=re.IGNORECASE | re.DOTALL,
        )
    return result


def _ensure_yellow_styles(styles_text: str, source_style_ids: set[int]) -> tuple[str, dict[int, int]]:
    fills_match = _section_match(styles_text, "fills")
    if fills_match is None:
        raise OoxmlPatchError("fills section is missing from styles.xml")
    fill_fragments = _child_elements(fills_match.group("inner"), "fill")
    yellow_fill_id = next(
        (index for index, fragment in enumerate(fill_fragments) if _is_solid_yellow_fill(fragment)),
        None,
    )
    if yellow_fill_id is None:
        prefix = _element_prefix(fill_fragments[0]) if fill_fragments else ""
        yellow_fill = (
            f"<{prefix}fill><{prefix}patternFill patternType=\"solid\">"
            f"<{prefix}fgColor rgb=\"FFFFFF00\"/><{prefix}bgColor indexed=\"64\"/>"
            f"</{prefix}patternFill></{prefix}fill>"
        )
        yellow_fill_id = len(fill_fragments)
        styles_text = _append_to_section(styles_text, fills_match, yellow_fill, len(fill_fragments) + 1)

    cell_xfs_match = _section_match(styles_text, "cellXfs")
    if cell_xfs_match is None:
        raise OoxmlPatchError("cellXfs section is missing from styles.xml")
    xf_fragments = _child_elements(cell_xfs_match.group("inner"), "xf")
    if not xf_fragments:
        raise OoxmlPatchError("cellXfs contains no styles")

    yellow_style_by_source: dict[int, int] = {}
    appended: list[str] = []
    available = list(xf_fragments)
    for source_style_id in sorted(source_style_ids):
        base_style_id = source_style_id if 0 <= source_style_id < len(xf_fragments) else 0
        base_fragment = xf_fragments[base_style_id]
        open_tag = base_fragment[: base_fragment.find(">") + 1]
        if _attribute_value(open_tag, "fillId") == str(yellow_fill_id) and (
            _attribute_value(open_tag, "applyFill") or "1"
        ) != "0":
            yellow_style_by_source[source_style_id] = base_style_id
            continue

        desired = _replace_element_start_tag(
            base_fragment,
            _set_attribute(_set_attribute(open_tag, "fillId", str(yellow_fill_id)), "applyFill", "1"),
        )
        existing_style_id = next(
            (index for index, fragment in enumerate(available) if _semantic_xml_signature(fragment) == _semantic_xml_signature(desired)),
            None,
        )
        if existing_style_id is None:
            existing_style_id = len(available)
            available.append(desired)
            appended.append(desired)
        yellow_style_by_source[source_style_id] = existing_style_id

    if appended:
        current_match = _section_match(styles_text, "cellXfs")
        if current_match is None:
            raise OoxmlPatchError("cellXfs section disappeared from styles.xml")
        styles_text = _append_to_section(
            styles_text,
            current_match,
            "".join(appended),
            len(xf_fragments) + len(appended),
        )
    return styles_text, yellow_style_by_source


def _ensure_non_yellow_styles(styles_text: str, source_style_ids: set[int]) -> tuple[str, dict[int, int]]:
    """黄色変更セルを、同じ書式の非黄色styleへ戻す。"""
    fills_match = _section_match(styles_text, "fills")
    if fills_match is None:
        raise OoxmlPatchError("fills section is missing from styles.xml")
    fill_fragments = _child_elements(fills_match.group("inner"), "fill")
    yellow_fill_ids = {
        index
        for index, fragment in enumerate(fill_fragments)
        if _is_solid_yellow_fill(fragment)
    }

    cell_xfs_match = _section_match(styles_text, "cellXfs")
    if cell_xfs_match is None:
        raise OoxmlPatchError("cellXfs section is missing from styles.xml")
    xf_fragments = _child_elements(cell_xfs_match.group("inner"), "xf")
    if not xf_fragments:
        raise OoxmlPatchError("cellXfs contains no styles")

    non_yellow_style_by_source: dict[int, int] = {}
    available = list(xf_fragments)
    appended: list[str] = []
    for source_style_id in sorted(source_style_ids):
        base_style_id = source_style_id if 0 <= source_style_id < len(available) else 0
        base_fragment = available[base_style_id]
        base_open_tag = base_fragment[: base_fragment.find(">") + 1]
        try:
            base_fill_id = int(_attribute_value(base_open_tag, "fillId") or "0")
        except ValueError:
            base_fill_id = 0
        if base_fill_id not in yellow_fill_ids:
            non_yellow_style_by_source[source_style_id] = base_style_id
            continue

        base_signature = _xf_without_fill_signature(base_fragment)
        matching_style_id: int | None = None
        for candidate_id, candidate in enumerate(available):
            candidate_open_tag = candidate[: candidate.find(">") + 1]
            try:
                candidate_fill_id = int(_attribute_value(candidate_open_tag, "fillId") or "0")
            except ValueError:
                candidate_fill_id = 0
            if candidate_fill_id in yellow_fill_ids:
                continue
            if _xf_without_fill_signature(candidate) == base_signature:
                matching_style_id = candidate_id
                break

        if matching_style_id is None:
            restored_open_tag = _set_attribute(_set_attribute(base_open_tag, "fillId", "0"), "applyFill", "0")
            restored = _replace_element_start_tag(base_fragment, restored_open_tag)
            matching_style_id = next(
                (
                    index
                    for index, candidate in enumerate(available)
                    if _semantic_xml_signature(candidate) == _semantic_xml_signature(restored)
                ),
                None,
            )
            if matching_style_id is None:
                matching_style_id = len(available)
                available.append(restored)
                appended.append(restored)
        non_yellow_style_by_source[source_style_id] = matching_style_id

    if appended:
        current_match = _section_match(styles_text, "cellXfs")
        if current_match is None:
            raise OoxmlPatchError("cellXfs section disappeared from styles.xml")
        styles_text = _append_to_section(
            styles_text,
            current_match,
            "".join(appended),
            len(xf_fragments) + len(appended),
        )
    return styles_text, non_yellow_style_by_source


def _xf_without_fill_signature(fragment: str) -> tuple[Any, ...]:
    try:
        root = ElementTree.fromstring(fragment)
    except ElementTree.ParseError:
        return (re.sub(r"\s+(?:fillId|applyFill)\s*=\s*(['\"]).*?\1", "", fragment),)

    def signature(element: ElementTree.Element, *, root_element: bool = False) -> tuple[Any, ...]:
        ignored = {"fillId", "applyFill"} if root_element else set()
        return (
            _local_name(element.tag),
            tuple(
                sorted(
                    (_local_name(key), value)
                    for key, value in element.attrib.items()
                    if _local_name(key) not in ignored
                )
            ),
            (element.text or "").strip(),
            tuple(signature(child) for child in element),
        )

    return signature(root, root_element=True)


def _section_match(xml_text: str, local_name: str) -> re.Match[str] | None:
    return re.search(
        rf"(?P<open><(?P<prefix>{_TAG_PREFIX}){local_name}\b[^>]*>)"
        rf"(?P<inner>.*?)"
        rf"(?P<close></(?P=prefix){local_name}\s*>)",
        xml_text,
        re.IGNORECASE | re.DOTALL,
    )


def _child_elements(inner_xml: str, local_name: str) -> list[str]:
    return [
        match.group(0)
        for match in re.finditer(
            rf"(?:<{_TAG_PREFIX}{local_name}\b[^>]*?/\s*>"
            rf"|<{_TAG_PREFIX}{local_name}\b[^>]*>.*?</{_TAG_PREFIX}{local_name}\s*>)",
            inner_xml,
            re.IGNORECASE | re.DOTALL,
        )
    ]


def _append_to_section(xml_text: str, section: re.Match[str], fragment: str, count: int) -> str:
    open_tag = _set_attribute(section.group("open"), "count", str(count))
    replacement = open_tag + section.group("inner") + fragment + section.group("close")
    return xml_text[: section.start()] + replacement + xml_text[section.end() :]


def _is_solid_yellow_fill(fill_xml: str) -> bool:
    pattern_match = re.search(rf"<{_TAG_PREFIX}patternFill\b[^>]*>", fill_xml, re.IGNORECASE)
    if pattern_match is None or (_attribute_value(pattern_match.group(0), "patternType") or "").lower() != "solid":
        return False
    color_match = re.search(rf"<{_TAG_PREFIX}fgColor\b[^>]*/?\s*>", fill_xml, re.IGNORECASE)
    rgb = _attribute_value(color_match.group(0), "rgb") if color_match else None
    return bool(rgb and rgb.upper().endswith("FFFF00"))


def _replace_element_start_tag(element_xml: str, start_tag: str) -> str:
    return start_tag + element_xml[element_xml.find(">") + 1 :]


def _semantic_xml_signature(fragment: str) -> tuple[Any, ...]:
    try:
        root = ElementTree.fromstring(fragment)
    except ElementTree.ParseError:
        return (re.sub(r"\s+", " ", fragment).strip(),)

    def signature(element: ElementTree.Element) -> tuple[Any, ...]:
        return (
            _local_name(element.tag),
            tuple(sorted((_local_name(key), value) for key, value in element.attrib.items())),
            (element.text or "").strip(),
            tuple(signature(child) for child in element),
        )

    return signature(root)


def _element_prefix(element_xml: str) -> str:
    match = re.match(r"<(?P<prefix>[A-Za-z_][\w.-]*:)?", element_xml)
    return (match.group("prefix") or "") if match else ""


def _attribute_value(tag: str, name: str) -> str | None:
    match = re.search(rf"\s{name}\s*=\s*(?P<quote>['\"])(?P<value>.*?)(?P=quote)", tag, re.IGNORECASE)
    return match.group("value") if match else None


def _set_attribute(tag: str, name: str, value: str) -> str:
    pattern = re.compile(rf"(?P<space>\s){name}\s*=\s*(?P<quote>['\"])(?P<value>.*?)(?P=quote)", re.IGNORECASE)
    match = pattern.search(tag)
    replacement = f'{match.group("space") if match else " "}{name}="{escape(value)}"'
    if match:
        return tag[: match.start()] + replacement + tag[match.end() :]
    insertion = tag.rfind("/>")
    if insertion < 0:
        insertion = tag.rfind(">")
    if insertion < 0:
        raise OoxmlPatchError(f"invalid XML start tag: {tag}")
    return tag[:insertion] + replacement + tag[insertion:]


def _remove_attribute(tag: str, name: str) -> str:
    return re.sub(
        rf"\s{name}\s*=\s*(?P<quote>['\"]).*?(?P=quote)",
        "",
        tag,
        flags=re.IGNORECASE,
    )


def _coordinate_parts(coordinate: str) -> tuple[str, int]:
    match = _CELL_REF_RE.fullmatch(coordinate.upper())
    if match is None:
        raise OoxmlPatchError(f"invalid cell coordinate: {coordinate}")
    return match.group(1), int(match.group(2))


def _column_number(column: str) -> int:
    number = 0
    for character in column.upper():
        number = number * 26 + (ord(character) - ord("A") + 1)
    return number


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _rewrite_archive(
    workbook_path: Path,
    entries: list[tuple[ZipInfo, bytes]],
    archive_comment: bytes,
    modifications: dict[str, bytes],
) -> None:
    workbook_path = workbook_path.resolve()
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{workbook_path.name}.",
            suffix=".tmp",
            dir=workbook_path.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)

        with ZipFile(temporary_path, "w", compression=ZIP_DEFLATED, allowZip64=True) as destination:
            destination.comment = archive_comment
            for info, original_payload in entries:
                destination.writestr(info, modifications.get(info.filename, original_payload))
        os.replace(temporary_path, workbook_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
