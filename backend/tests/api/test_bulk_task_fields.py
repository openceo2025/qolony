from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from app import main


def test_bulk_task_fields_returns_stale_failure_and_new_revision(monkeypatch) -> None:
    class FakeService:
        def bulk_update_task_fields(self, *, operator_user_id, parent_task_id, updates):
            assert operator_user_id == "operator"
            assert parent_task_id == "parent-1"
            assert updates == [
                {
                    "task_id": "task-ok",
                    "name": "更新名",
                    "assigned_manhour": 240,
                    "memo": "更新メモ",
                    "end_date": "2026-08-31",
                    "assignee_user_id": "user-b",
                }
            ]
            return {
                "changed": 1,
                "failed": 0,
                "results": [
                    {
                        "task_id": "task-ok",
                        "success": True,
                        "changed": True,
                        "message": "タスクを更新しました",
                        "warnings": ["日程警告"],
                    }
                ],
            }

    revisions = {
        "parent-1": "current-parent",
        "task-stale": "current-stale",
        "task-ok": "current-ok",
    }
    repository = SimpleNamespace(
        get_task=lambda task_id: SimpleNamespace(
            revision=revisions[task_id],
            Name=f"name-{task_id}",
            AssignedManHour=120,
            Memo="latest memo",
            EndDate=date(2026, 9, 1),
            AssigneeUserId="user-latest",
        )
    )
    monkeypatch.setattr(main, "task_revision", lambda task: task.revision)
    monkeypatch.setattr(
        main,
        "get_runtime",
        lambda: SimpleNamespace(service=FakeService(), repository=repository),
    )

    result = main.bulk_update_task_fields(
        main.BulkTaskFieldsBody(
            user_id="operator",
            parent_task_id="parent-1",
            expected_parent_revision="current-parent",
            updates=[
                main.BulkTaskFieldsRow(
                    task_id="task-stale",
                    name="古い編集",
                    assigned_manhour=120,
                    memo="",
                    end_date="2026-08-01",
                    assignee_user_id="user-a",
                    expected_revision="old-revision",
                ),
                main.BulkTaskFieldsRow(
                    task_id="task-ok",
                    name="更新名",
                    assigned_manhour=240,
                    memo="更新メモ",
                    end_date="2026-08-31",
                    assignee_user_id="user-b",
                    expected_revision="current-ok",
                ),
            ],
        )
    )

    assert result["status"] == "partial"
    assert result["changed"] == 1
    assert result["failed"] == 1
    assert result["results"][0]["success"] is False
    assert result["results"][0]["code"] == "task_conflict"
    assert result["results"][0]["revision"] == "current-stale"
    assert "別のユーザー" in result["results"][0]["message"]
    assert result["results"][0]["warnings"] == []
    assert result["results"][1] == {
        "task_id": "task-ok",
        "success": True,
        "changed": True,
        "message": "タスクを更新しました",
        "warnings": ["日程警告"],
        "revision": "current-ok",
    }
    assert result["parent_revision"] == "current-parent"


def test_bulk_task_fields_rejects_duplicate_rows_without_sending_second_to_service(monkeypatch) -> None:
    class FakeService:
        def bulk_update_task_fields(self, *, operator_user_id, parent_task_id, updates):
            assert operator_user_id == "operator"
            assert parent_task_id == "parent-1"
            assert len(updates) == 1
            return {
                "changed": 0,
                "failed": 0,
                "results": [
                    {
                        "task_id": "task-1",
                        "success": True,
                        "changed": False,
                        "message": "変更はありません",
                        "warnings": [],
                    }
                ],
            }

    repository = SimpleNamespace(
        get_task=lambda task_id: SimpleNamespace(revision=f"revision-{task_id}")
    )
    monkeypatch.setattr(main, "task_revision", lambda task: task.revision)
    monkeypatch.setattr(
        main,
        "get_runtime",
        lambda: SimpleNamespace(service=FakeService(), repository=repository),
    )
    common = {
        "task_id": "task-1",
        "name": "同じタスク",
        "assigned_manhour": 60,
        "memo": "",
        "end_date": "2026-08-01",
        "assignee_user_id": "user-a",
    }

    result = main.bulk_update_task_fields(
        main.BulkTaskFieldsBody(
            user_id="operator",
            parent_task_id="parent-1",
            updates=[main.BulkTaskFieldsRow(**common), main.BulkTaskFieldsRow(**common)],
        )
    )

    assert result["status"] == "partial"
    assert result["changed"] == 0
    assert result["failed"] == 1
    assert result["results"][0]["success"] is True
    assert result["results"][0]["revision"] == "revision-task-1"
    assert result["results"][1]["success"] is False
    assert "重複" in result["results"][1]["message"]
    assert result["parent_revision"] == "revision-parent-1"


def test_bulk_task_fields_rejects_stale_parent_before_any_row_update(monkeypatch) -> None:
    class FakeService:
        def bulk_update_task_fields(self, **_kwargs):
            pytest.fail("親タスク競合時に行更新を開始しています")

    repository = SimpleNamespace(
        get_task=lambda task_id: SimpleNamespace(revision="current-parent")
    )
    monkeypatch.setattr(main, "task_revision", lambda task: task.revision)
    monkeypatch.setattr(
        main,
        "get_runtime",
        lambda: SimpleNamespace(service=FakeService(), repository=repository),
    )

    with pytest.raises(main.TaskConflictError, match="親タスク"):
        main.bulk_update_task_fields(
            main.BulkTaskFieldsBody(
                user_id="operator",
                parent_task_id="parent-1",
                expected_parent_revision="stale-parent",
                updates=[],
            )
        )
