from __future__ import annotations

from datetime import date
from pathlib import Path

from openpyxl import Workbook

from app.iquavis.mapping_seed import (
    MappingSeedResult,
    QolonyRootTask,
    SheetImageSeed,
    build_visual_task_candidates,
    build_visible_gantt_date_map,
    detect_visible_gantt_sheet_names,
    extract_visual_items,
    load_uncompleted_iquavis_tasks,
    write_mapping_seed_workbook,
)


def _write_fixture(workbook_path: Path) -> None:
    workbook = Workbook()

    visible = workbook.active
    visible.title = "VisibleGantt"
    visible["D5"] = 2026
    visible["D6"] = 1
    visible["D7"] = 5
    visible["H6"] = 2
    visible["H7"] = 2
    visible["B11"] = "LHドア"
    visible["H12"] = "▽承認図の出図"

    task_sheet = workbook.create_sheet("551個人業務計画 (1)")
    task_sheet.append(
        [
            "Id",
            "Name",
            "Type",
            "StartDate",
            "EndDate",
            "ProjectId",
            "TaskDomainId",
            "TaskDomainRowNumber",
            "ParentTaskId",
            "Progress.Status",
            "Progress.ProgressRate",
            "Assigns",
        ]
    )
    task_sheet.append(
        [
            "TASK-1",
            "【業務依頼】VR05 承認図の出図",
            "Task",
            date(2026, 1, 15),
            date(2026, 2, 10),
            "P1",
            "DOMAIN-1",
            4,
            "",
            "DuringStart",
            50,
            '[{"LoginId":"U0001","Name":"山田"}]',
        ]
    )
    task_sheet.append(
        [
            "TASK-2",
            "VR05 完了済み",
            "Task",
            date(2026, 1, 1),
            date(2026, 1, 20),
            "P1",
            "DOMAIN-2",
            5,
            "",
            "Completion",
            100,
            '[{"LoginId":"U0001","Name":"山田"}]',
        ]
    )

    other_sheet = workbook.create_sheet("551_全体日程_20240401")
    other_sheet.append(
        [
            "Id",
            "Name",
            "Type",
            "StartDate",
            "EndDate",
            "ProjectId",
            "TaskDomainId",
            "TaskDomainRowNumber",
            "ParentTaskId",
            "Progress.Status",
            "Progress.ProgressRate",
            "Assigns",
        ]
    )

    workbook.save(workbook_path)
    workbook.close()


def test_detect_visible_gantt_sheet_names(tmp_path: Path) -> None:
    workbook_path = tmp_path / "fixture.xlsx"
    _write_fixture(workbook_path)

    sheet_names = detect_visible_gantt_sheet_names(workbook_path)

    assert sheet_names == ["VisibleGantt"]


def test_extract_visual_items_and_build_date_map(tmp_path: Path) -> None:
    workbook_path = tmp_path / "fixture.xlsx"
    _write_fixture(workbook_path)

    from openpyxl import load_workbook

    workbook = load_workbook(workbook_path, read_only=False, data_only=True)
    try:
        date_map = build_visible_gantt_date_map(workbook["VisibleGantt"])
    finally:
        workbook.close()

    items = extract_visual_items(
        workbook_path,
        sheet_names=["VisibleGantt"],
        target_projects=["VR05"],
    )

    assert date_map[4] == date(2026, 1, 5)
    assert date_map[8] == date(2026, 2, 2)
    assert any(item.kind == "task_domain" and item.path == "LHドア" for item in items)
    assert any(
        item.kind == "visual_task"
        and item.label_raw == "▽承認図の出図"
        and item.visual_date == date(2026, 2, 2)
        for item in items
    )


def test_load_uncompleted_tasks_and_match_visual_candidates(tmp_path: Path) -> None:
    workbook_path = tmp_path / "fixture.xlsx"
    _write_fixture(workbook_path)

    tasks = load_uncompleted_iquavis_tasks(
        workbook_path,
        sheet_names=["551個人業務計画 (1)", "551_全体日程_20240401"],
        target_projects=["VR05"],
    )
    items = extract_visual_items(
        workbook_path,
        sheet_names=["VisibleGantt"],
        target_projects=["VR05"],
    )
    candidates = build_visual_task_candidates(items, tasks)

    assert len(tasks) == 1
    assert tasks[0].iquavis_task_id == "TASK-1"
    assert tasks[0].assignee_login_id == "U0001"
    assert candidates
    assert candidates[0].task.iquavis_task_id == "TASK-1"
    assert candidates[0].visual_item.label_raw == "▽承認図の出図"


def test_write_mapping_seed_workbook_includes_sheet_images_in_mapping(tmp_path: Path) -> None:
    output_path = tmp_path / "seed.xlsx"
    image_path = tmp_path / "Sheet3_001_image1.png"
    image_path.write_bytes(b"png")
    result = MappingSeedResult(
        root_tasks=[
            QolonyRootTask(
                task_id="ROOT-1",
                name="VR05",
                task_type="Task",
                has_ecr=False,
                has_business_request=False,
                has_design_and_mass_production=False,
            )
        ],
        visual_sheet_names=["Sheet3"],
        visual_items=[],
        sheet_images=[
            SheetImageSeed(
                source_sheet="Sheet3",
                image_index=1,
                image_name="image1.png",
                from_cell="A1",
                to_cell="W19",
                extracted_path=image_path,
            )
        ],
        task_rows=[],
        candidates=[],
        output_path=output_path,
    )

    write_mapping_seed_workbook(result)

    from openpyxl import load_workbook

    workbook = load_workbook(output_path, read_only=False, data_only=True)
    try:
        image_rows = list(workbook["見た目画像一覧"].iter_rows(min_row=2, max_row=2, values_only=True))
        mapping_rows = list(workbook["マッピング"].iter_rows(min_row=2, max_row=2, values_only=True))
    finally:
        workbook.close()

    assert image_rows[0][0] == "Sheet3"
    assert image_rows[0][2] == "image1.png"
    assert mapping_rows[0][1] == "sheet_image"
    assert mapping_rows[0][5] == "A1:W19"
