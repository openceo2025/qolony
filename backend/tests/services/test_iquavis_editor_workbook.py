from __future__ import annotations

import os
from datetime import date
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from app.iquavis import ooxml_patch
from app.iquavis.editor_models import IquavisEditorQolonyUpdate, IquavisTaskSyncProperties
from app.iquavis.editor_workbook import OpenpyxlIquavisEditorWorkbookAdapter
from app.iquavis.ooxml_patch import OoxmlCellPatch, patch_ooxml_cells


def _save_workbook(path):
    workbook = Workbook()
    default_sheet = workbook.active
    workbook.remove(default_sheet)

    sync = workbook.create_sheet("同期しているidリスト")
    sync.append(["iQUAVIS ID", "種別", "強制同期"])
    sync.append(["TASK-1", "task", "iquavis→qolony"])

    tasks = workbook.create_sheet("全タスク")
    tasks.append(
        [
            "読み取り対象",
            "ParentTaskId",
            "Id",
            "Name",
            "Type",
            "StartDate",
            "EndDate",
            "ProjectId",
            "TaskDomainId",
            "AssignedManHour",
            "Progress.ActualManHour",
            "Progress.Status",
            "Progress.ProgressRate",
            "Progress.Memo",
            "Assigns",
            "CreateUserId",
        ]
    )
    tasks.append(
        [
            "●",
            "",
            "TASK-1",
            "Remote Task",
            "Task",
            date(2026, 4, 1),
            date(2026, 4, 8),
            "P1",
            "DOMAIN-1",
            120,
            30,
            "DuringStart",
            25,
            None,
            '[{"Id":"USER-1","LoginId":"U0002","Name":"member"}]',
            "USER-CREATOR",
        ]
    )
    tasks["D2"].font = Font(name="Meiryo UI", bold=True)
    tasks["D2"].border = Border(left=Side(style="thin", color="FF112233"))
    tasks["D2"].alignment = Alignment(horizontal="center", vertical="center")
    tasks["D2"].number_format = "@"
    tasks["D2"].fill = PatternFill(fill_type="solid", fgColor="FF00FF00")

    domains = workbook.create_sheet("task domains")
    domains.append(["Id", "Name", "ProjectId", "FullName", "ParentTaskDomainId"])
    domains.append(["DOMAIN-ROOT", "Root Domain", "P1", "Root Domain", ""])
    domains.append(["DOMAIN-1", "Child Domain", "P1", "Root Domain.Child Domain", "DOMAIN-ROOT"])

    original = workbook.create_sheet("__TASKS_P1")
    original.sheet_state = "hidden"
    original.append(
        [
            "Id",
            "Name",
            "Type",
            "StartDate",
            "EndDate",
            "ProjectId",
            "TaskDomainId",
            "AssignedManHour",
            "Progress.ActualManHour",
            "Progress.Status",
            "Progress.ProgressRate",
            "Progress.Memo",
        ]
    )
    original.append(
        [
            "TASK-1",
            "Previous Task",
            "Task",
            date(2026, 4, 1),
            date(2026, 4, 5),
            "P1",
            "DOMAIN-1",
            90,
            0,
            "NotStart",
            0,
            "previous memo",
        ]
    )
    workbook.save(path)
    workbook.close()


def _inject_archive_sentinels(path) -> None:
    with ZipFile(path, "r") as source:
        entries = [(info, source.read(info)) for info in source.infolist()]
        comment = source.comment

    payloads = {info.filename: payload for info, payload in entries}
    task_sheet_name = "xl/worksheets/sheet2.xml"
    task_sheet_xml = payloads[task_sheet_name]
    drawing_reference = (
        b'<drawing xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        b'r:id="rIdDrawingSentinel"/>'
    )
    payloads[task_sheet_name] = task_sheet_xml.replace(b"</worksheet>", drawing_reference + b"</worksheet>")

    content_types_addition = (
        b'<Override PartName="/xl/drawings/drawing1.xml" '
        b'ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/>'
        b'<Override PartName="/xl/calcChain.xml" '
        b'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.calcChain+xml"/>'
        b'<Override PartName="/xl/sharedStrings.xml" '
        b'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        b'<Override PartName="/xl/vbaProject.bin" ContentType="application/vnd.ms-office.vbaProject"/>'
    )
    payloads["[Content_Types].xml"] = payloads["[Content_Types].xml"].replace(
        b"</Types>",
        content_types_addition + b"</Types>",
    )
    workbook_relationships_addition = (
        b'<Relationship Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/calcChain" '
        b'Target="calcChain.xml" Id="rIdCalcSentinel"/>'
        b'<Relationship Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" '
        b'Target="sharedStrings.xml" Id="rIdStringsSentinel"/>'
        b'<Relationship Type="http://schemas.microsoft.com/office/2006/relationships/vbaProject" '
        b'Target="vbaProject.bin" Id="rIdVbaSentinel"/>'
    )
    payloads["xl/_rels/workbook.xml.rels"] = payloads["xl/_rels/workbook.xml.rels"].replace(
        b"</Relationships>",
        workbook_relationships_addition + b"</Relationships>",
    )

    sentinel_entries = {
        "xl/worksheets/_rels/sheet2.xml.rels": (
            b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            b'<Relationship Id="rIdDrawingSentinel" '
            b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing" '
            b'Target="../drawings/drawing1.xml"/></Relationships>'
        ),
        "xl/drawings/drawing1.xml": (
            b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            b'<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
            b'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            b'<xdr:sp><xdr:nvSpPr/><xdr:spPr/><xdr:txBody><a:bodyPr/><a:lstStyle/>'
            b'<a:p><a:r><a:t>DRAWING_SENTINEL</a:t></a:r></a:p></xdr:txBody></xdr:sp></xdr:wsDr>'
        ),
        "xl/calcChain.xml": (
            b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            b'<calcChain xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            b'<c r="A1" i="2"/></calcChain>'
        ),
        "xl/sharedStrings.xml": (
            b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            b'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="1" uniqueCount="1">'
            b'<si><t>SHARED_STRING_SENTINEL</t></si></sst>'
        ),
        "xl/vbaProject.bin": b"VBA-SENTINEL\x00\x01\x02\xff",
    }

    temporary_path = path.with_suffix(".sentinel.tmp")
    try:
        with ZipFile(temporary_path, "w", compression=ZIP_DEFLATED) as destination:
            destination.comment = comment
            written = set()
            for info, _ in entries:
                destination.writestr(info, payloads[info.filename])
                written.add(info.filename)
            for name, payload in sentinel_entries.items():
                if name not in written:
                    destination.writestr(name, payload)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _archive_payloads(path) -> dict[str, bytes]:
    with ZipFile(path, "r") as archive:
        return {info.filename: archive.read(info) for info in archive.infolist()}


def test_openpyxl_editor_workbook_adapter_loads_snapshot_and_writes_updates(tmp_path) -> None:
    workbook_path = tmp_path / "iquavis_editor.xlsm"
    _save_workbook(workbook_path)
    _inject_archive_sentinels(workbook_path)
    archive_before = _archive_payloads(workbook_path)

    adapter = OpenpyxlIquavisEditorWorkbookAdapter()
    snapshot = adapter.load_snapshot(workbook_path)

    target_by_id = {target.iquavis_id: target for target in snapshot.synced_targets}
    assert set(target_by_id) == {"TASK-1", "DOMAIN-ROOT", "DOMAIN-1"}
    assert target_by_id["TASK-1"].force_sync == "iquavis_to_qolony"
    assert len(snapshot.tasks) == 1
    assert len(snapshot.task_domains) == 2
    assert snapshot.task_domains[1].iquavis_task_domain_id == "DOMAIN-1"
    assert snapshot.task_domains[1].parent_task_domain_id == "DOMAIN-ROOT"
    assert snapshot.tasks[0].iquavis_task_id == "TASK-1"
    assert snapshot.tasks[0].assigned_minutes == 120
    assert snapshot.tasks[0].assignee is not None
    assert snapshot.tasks[0].assignee.iquavis_user_id == "USER-1"
    assert snapshot.tasks[0].creator is not None
    assert snapshot.tasks[0].creator.iquavis_user_id == "USER-CREATOR"
    assert len(snapshot.task_sync_records) == 1
    assert snapshot.task_sync_records[0].previous is not None
    assert snapshot.task_sync_records[0].previous.name == "Previous Task"
    assert snapshot.task_sync_records[0].iquavis_current.name == "Remote Task"

    write_result = adapter.write_qolony_updates(
        workbook_path,
        [
            IquavisEditorQolonyUpdate(
                iquavis_id="TASK-1",
                properties=IquavisTaskSyncProperties(
                    name="Local Task",
                    start_date=date(2026, 4, 2),
                    end_date=date(2026, 4, 9),
                    assigned_minutes=150,
                    actual_minutes=60,
                    status="DuringStart",
                    progress_rate=75,
                    memo="",
                ),
                field_names=(
                    "name",
                    "start_date",
                    "end_date",
                    "actual_minutes",
                    "status",
                    "progress_rate",
                    "memo",
                ),
            )
        ],
    )
    clear_result = adapter.clear_force_sync_requests(workbook_path, ["TASK-1"])

    assert write_result.updated_ids == ["TASK-1"]
    assert clear_result.cleared_force_sync_ids == ["TASK-1"]

    archive_after = _archive_payloads(workbook_path)
    assert set(archive_after) == set(archive_before)
    changed_parts = {
        "xl/styles.xml",
        "xl/worksheets/sheet1.xml",
        "xl/worksheets/sheet2.xml",
    }
    for part_name, original_payload in archive_before.items():
        if part_name not in changed_parts:
            assert archive_after[part_name] == original_payload, part_name
    assert b'r:id="rIdDrawingSentinel"' in archive_after["xl/worksheets/sheet2.xml"]
    assert archive_after["xl/drawings/drawing1.xml"] == archive_before["xl/drawings/drawing1.xml"]
    assert archive_after["xl/worksheets/_rels/sheet2.xml.rels"] == archive_before[
        "xl/worksheets/_rels/sheet2.xml.rels"
    ]
    assert archive_after["xl/vbaProject.bin"] == archive_before["xl/vbaProject.bin"]

    workbook = load_workbook(workbook_path, read_only=False, data_only=True, keep_vba=False)
    try:
        assert workbook["全タスク"]["D2"].value == "Local Task"
        assert workbook["全タスク"]["F2"].value.date() == date(2026, 4, 2)
        assert workbook["全タスク"]["G2"].value.date() == date(2026, 4, 9)
        # AssignedManHour はiQUAVIS側でreadOnlyのため書き換えない。
        assert workbook["全タスク"]["J2"].value == 120
        assert workbook["全タスク"]["K2"].value == 60
        assert workbook["全タスク"]["M2"].value == 75
        assert workbook["全タスク"]["N2"].value is None
        assert workbook["全タスク"]["D2"].font.bold is True
        assert workbook["全タスク"]["D2"].font.name == "Meiryo UI"
        assert workbook["全タスク"]["D2"].border.left.style == "thin"
        assert workbook["全タスク"]["D2"].alignment.horizontal == "center"
        assert workbook["全タスク"]["D2"].number_format == "@"
        assert workbook["全タスク"]["D2"].fill.fill_type == "solid"
        assert workbook["全タスク"]["D2"].fill.fgColor.rgb.endswith("FFFF00")
        assert workbook["全タスク"]["K2"].fill.fgColor.rgb.endswith("FFFF00")
        assert workbook["全タスク"]["M2"].fill.fgColor.rgb.endswith("FFFF00")
        assert workbook["全タスク"]["N2"].fill.fgColor.rgb.endswith("FFFF00")
        assert workbook["__TASKS_P1"]["B2"].value == "Previous Task"
        assert workbook["__TASKS_P1"].sheet_state == "hidden"
        assert workbook["同期しているidリスト"]["C2"].value in (None, "")
    finally:
        workbook.close()

    restore_result = adapter.restore_iquavis_values(
        workbook_path,
        [
            IquavisEditorQolonyUpdate(
                iquavis_id="TASK-1",
                properties=IquavisTaskSyncProperties(
                    name="Previous Task",
                    start_date=date(2026, 4, 1),
                    end_date=date(2026, 4, 8),
                    assigned_minutes=90,
                    actual_minutes=30,
                    status="NotStart",
                    progress_rate=25,
                    memo="Previous memo",
                ),
                field_names=(
                    "name",
                    "start_date",
                    "end_date",
                    "assigned_minutes",
                    "actual_minutes",
                    "status",
                    "progress_rate",
                    "memo",
                ),
            )
        ],
    )
    assert restore_result.updated_ids == ["TASK-1"]
    restored_archive = _archive_payloads(workbook_path)
    assert restored_archive["xl/vbaProject.bin"] == archive_before["xl/vbaProject.bin"]
    workbook = load_workbook(workbook_path, read_only=False, data_only=True, keep_vba=False)
    try:
        assert workbook["全タスク"]["D2"].value == "Previous Task"
        assert workbook["全タスク"]["J2"].value == 90
        assert not (
            workbook["全タスク"]["D2"].fill.fill_type == "solid"
            and workbook["全タスク"]["D2"].fill.fgColor.rgb
            and workbook["全タスク"]["D2"].fill.fgColor.rgb.endswith("FFFF00")
        )
        assert workbook["__TASKS_P1"]["B2"].value == "Previous Task"
    finally:
        workbook.close()

    no_write_before = workbook_path.read_bytes()
    no_write_result = adapter.write_qolony_updates(
        workbook_path,
        [
            IquavisEditorQolonyUpdate(
                iquavis_id="TASK-1",
                properties=IquavisTaskSyncProperties(
                    name="Ignored",
                    start_date=date(2026, 4, 2),
                    end_date=date(2026, 4, 9),
                    assigned_minutes=999,
                    actual_minutes=999,
                    status="Done",
                ),
                field_names=("assigned_minutes", "unsupported_field"),
            )
        ],
    )
    assert no_write_result.updated_ids == []
    assert workbook_path.read_bytes() == no_write_before


def test_memo_alias_always_prefers_progress_memo_when_task_note_coexists(tmp_path) -> None:
    workbook_path = tmp_path / "iquavis_editor_memo_priority.xlsm"
    _save_workbook(workbook_path)
    workbook = load_workbook(workbook_path, read_only=False, data_only=False, keep_vba=False)
    try:
        workbook["全タスク"]["N2"] = "visible progress memo"
        workbook["全タスク"]["Q1"] = "Note"
        workbook["全タスク"]["Q2"] = "visible task note"
        workbook["__TASKS_P1"]["L2"] = "hidden progress memo"
        workbook["__TASKS_P1"]["M1"] = "Note"
        workbook["__TASKS_P1"]["M2"] = "hidden task note"
        workbook.save(workbook_path)
    finally:
        workbook.close()

    adapter = OpenpyxlIquavisEditorWorkbookAdapter()
    snapshot = adapter.load_snapshot(workbook_path)

    assert snapshot.tasks[0].note == "visible progress memo"
    assert snapshot.task_sync_records[0].iquavis_current.memo == "visible progress memo"
    assert snapshot.task_sync_records[0].previous is not None
    assert snapshot.task_sync_records[0].previous.memo == "hidden progress memo"

    result = adapter.write_qolony_updates(
        workbook_path,
        [
            IquavisEditorQolonyUpdate(
                iquavis_id="TASK-1",
                properties=IquavisTaskSyncProperties(
                    name="Remote Task",
                    start_date=date(2026, 4, 1),
                    end_date=date(2026, 4, 8),
                    assigned_minutes=120,
                    actual_minutes=30,
                    status="DuringStart",
                    progress_rate=25,
                    memo="QOLONY progress memo",
                ),
                field_names=("memo",),
            )
        ],
    )

    assert result.updated_ids == ["TASK-1"]
    workbook = load_workbook(workbook_path, read_only=False, data_only=True, keep_vba=False)
    try:
        assert workbook["全タスク"]["N2"].value == "QOLONY progress memo"
        assert workbook["全タスク"]["Q2"].value == "visible task note"
    finally:
        workbook.close()


def test_workbook_writer_is_atomic_when_a_requested_field_column_is_missing(tmp_path) -> None:
    workbook_path = tmp_path / "iquavis_editor_missing_memo.xlsm"
    _save_workbook(workbook_path)
    workbook = load_workbook(workbook_path, read_only=False, data_only=False, keep_vba=False)
    try:
        workbook["全タスク"]["N1"] = "UnknownMemo"
        workbook.save(workbook_path)
    finally:
        workbook.close()
    before = workbook_path.read_bytes()

    result = OpenpyxlIquavisEditorWorkbookAdapter().write_qolony_updates(
        workbook_path,
        [
            IquavisEditorQolonyUpdate(
                iquavis_id="TASK-1",
                properties=IquavisTaskSyncProperties(
                    name="Must Not Partially Write",
                    start_date=date(2026, 4, 2),
                    end_date=date(2026, 4, 9),
                    assigned_minutes=120,
                    actual_minutes=60,
                    status="DuringStart",
                    progress_rate=50,
                    memo="missing column",
                ),
                field_names=("name", "memo"),
            )
        ],
    )

    assert result.updated_ids == []
    assert workbook_path.read_bytes() == before


def test_ooxml_patch_indexes_one_target_sheet_once_for_many_cell_updates(tmp_path, monkeypatch) -> None:
    workbook_path = tmp_path / "many_updates.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Batch"
    worksheet.append(["Id", "Value"])
    for row_number in range(2, 502):
        worksheet.append([f"TASK-{row_number}", f"before-{row_number}"])
    workbook.save(workbook_path)
    workbook.close()

    index_count = 0
    original_index = ooxml_patch._index_worksheet

    def counted_index(sheet_text: str):
        nonlocal index_count
        index_count += 1
        return original_index(sheet_text)

    monkeypatch.setattr(ooxml_patch, "_index_worksheet", counted_index)
    patch_ooxml_cells(
        workbook_path,
        [
            OoxmlCellPatch(
                sheet_name="Batch",
                coordinate=f"B{row_number}",
                value=f"after-{row_number}",
                yellow_fill=True,
            )
            for row_number in range(2, 502)
        ],
    )

    assert index_count == 1
    workbook = load_workbook(workbook_path, read_only=False, data_only=True)
    try:
        assert workbook["Batch"]["B2"].value == "after-2"
        assert workbook["Batch"]["B501"].value == "after-501"
        assert workbook["Batch"]["B2"].fill.fgColor.rgb.endswith("FFFF00")
        assert workbook["Batch"]["B501"].fill.fgColor.rgb.endswith("FFFF00")
    finally:
        workbook.close()


def test_openpyxl_editor_workbook_adapter_auto_targets_all_exported_tasks_and_domains(tmp_path) -> None:
    workbook_path = tmp_path / "iquavis_editor.xlsm"
    workbook = Workbook()
    default_sheet = workbook.active
    workbook.remove(default_sheet)

    tasks = workbook.create_sheet("all tasks")
    tasks.append(
        [
            "Id",
            "Name",
            "Type",
            "StartDate",
            "EndDate",
            "ProjectId",
            "TaskDomainId",
        ]
    )
    tasks.append(["TASK-A", "Task A", "Task", date(2026, 6, 1), date(2026, 6, 2), "P1", "DOMAIN-A"])

    domains = workbook.create_sheet("task domains")
    domains.append(["Id", "Name", "ProjectId", "FullName", "ParentTaskDomainId"])
    domains.append(["DOMAIN-A", "Domain A", "P1", "Domain A", ""])
    workbook.save(workbook_path)
    workbook.close()

    snapshot = OpenpyxlIquavisEditorWorkbookAdapter().load_snapshot(workbook_path)
    target_by_id = {target.iquavis_id: target for target in snapshot.synced_targets}

    assert target_by_id["DOMAIN-A"].kind == "task_domain"
    assert target_by_id["TASK-A"].kind == "task"
    assert len(snapshot.task_sync_records) == 1
    assert snapshot.task_sync_records[0].iquavis_id == "TASK-A"


def test_openpyxl_editor_workbook_adapter_reads_root_only_domain_sheet_without_parent_column(tmp_path) -> None:
    workbook_path = tmp_path / "iquavis_editor.xlsm"
    workbook = Workbook()
    default_sheet = workbook.active
    workbook.remove(default_sheet)

    domains = workbook.create_sheet("VC74_B-Gr_550_幹業務_ver2タスクドメイン")
    domains.append(["表示順", "Id", "Name", "ProjectId", "FullName"])
    domains.append(["1", "DOMAIN-ROOT", "VC74_B-Gr_550_幹業務_ver2", "P-NEW", "VC74_B-Gr_550_幹業務_ver2"])

    tasks = workbook.create_sheet("VC74_B-Gr_550_幹業務_ver2全タスク")
    tasks.append(["Id", "Name", "Type", "StartDate", "EndDate", "ProjectId", "TaskDomainId"])
    tasks.append(["TASK-NEW", "New Project Task", "Task", date(2026, 6, 1), date(2026, 6, 2), "P-NEW", "DOMAIN-ROOT"])
    workbook.save(workbook_path)
    workbook.close()

    snapshot = OpenpyxlIquavisEditorWorkbookAdapter().load_snapshot(workbook_path)
    assert len(snapshot.task_domains) == 1
    assert snapshot.task_domains[0].iquavis_task_domain_id == "DOMAIN-ROOT"
    assert snapshot.task_domains[0].parent_task_domain_id is None
    target_by_id = {target.iquavis_id: target for target in snapshot.synced_targets}
    assert target_by_id["DOMAIN-ROOT"].kind == "task_domain"
    assert target_by_id["TASK-NEW"].kind == "task"
