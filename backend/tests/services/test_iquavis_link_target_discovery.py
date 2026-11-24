from __future__ import annotations

from datetime import date

from openpyxl import Workbook, load_workbook

from app.iquavis.link_target_discovery import append_tasks_under_linked_domains


def test_append_tasks_under_linked_domains_adds_only_unregistered_descendant_tasks(tmp_path) -> None:
    editor_path = tmp_path / "iquavis_editer_latest_ver.xlsm"
    link_targets_path = tmp_path / "iquavis_link_targets.xlsx"

    editor = Workbook()
    domain_sheet = editor.active
    domain_sheet.title = "current_task_domains"
    domain_sheet.append(["Id", "Name", "ProjectId", "ParentTaskDomainId"])
    domain_sheet.append(["DOMAIN-ROOT", "root", "P1", ""])
    domain_sheet.append(["DOMAIN-CHILD", "child", "P1", "DOMAIN-ROOT"])
    domain_sheet.append(["DOMAIN-OTHER", "other", "P1", ""])
    task_sheet = editor.create_sheet("current_tasks")
    task_sheet.append(["Id", "Name", "Type", "StartDate", "EndDate", "TaskDomainId", "ParentTaskId"])
    task_sheet.append(["TASK-OLD", "old", "Task", date(2026, 4, 30), date(2026, 5, 2), "DOMAIN-CHILD", ""])
    task_sheet.append(["TASK-EXISTING", "existing", "Task", date(2026, 5, 1), date(2026, 5, 3), "DOMAIN-CHILD", ""])
    task_sheet.append(["TASK-NEW", "new", "Task", date(2026, 5, 2), date(2026, 5, 4), "DOMAIN-CHILD", "TASK-EXISTING"])
    task_sheet.append(["TASK-OTHER", "other", "Task", date(2026, 5, 2), date(2026, 5, 4), "DOMAIN-OTHER", ""])
    original_sheet = editor.create_sheet("__TASKS_previous")
    original_sheet.append(["Id", "Name", "Type", "StartDate", "EndDate", "TaskDomainId"])
    original_sheet.append(["TASK-PREVIOUS", "previous", "Task", date(2026, 5, 2), date(2026, 5, 4), "DOMAIN-CHILD"])
    editor.save(editor_path)

    link_targets = Workbook()
    tasks = link_targets.active
    tasks.title = "QOLONY_linked_tasks"
    tasks.append(["use", "iquavis_task_id", "qolony_task_id", "qolony_parent_task_id", "force_sync", "note"])
    tasks.append(["0", "TASK-EXISTING", "", "", "", "disabled rows still count as registered"])
    domains = link_targets.create_sheet("QOLONY_linked_task_domains")
    domains.append(["use", "iquavis_task_domain_id", "qolony_task_id", "qolony_parent_task_id", "force_sync", "note"])
    domains.append(["1", "DOMAIN-ROOT", "", "", "", ""])
    link_targets.save(link_targets_path)

    result = append_tasks_under_linked_domains(
        editor_workbook_path=editor_path,
        link_targets_workbook_path=link_targets_path,
        start_date_from=date(2026, 5, 1),
    )

    assert result.linked_task_domain_count == 1
    assert result.descendant_task_domain_count == 2
    assert result.candidate_task_count == 2
    assert result.already_registered_task_count == 1
    assert result.appended_task_count == 1
    assert result.appended_task_ids == ["task-new"]

    saved = load_workbook(link_targets_path, data_only=True)
    rows = list(saved["QOLONY_linked_tasks"].iter_rows(values_only=True))
    assert [row[1] for row in rows[1:]] == ["TASK-EXISTING", "task-new"]
    assert "source_sheet=current_tasks" in rows[2][5]
    assert "parent_task_id=task-existing" in rows[2][5]
