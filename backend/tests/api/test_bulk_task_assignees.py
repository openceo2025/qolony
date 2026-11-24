from __future__ import annotations

from types import SimpleNamespace

from app import main


def test_bulk_task_assignees_endpoint_returns_partial_row_results(monkeypatch) -> None:
    expected = {
        "changed": 1,
        "failed": 1,
        "results": [
            {
                "task_id": "task-ok",
                "assignee_user_id": "user-b",
                "success": True,
                "changed": True,
                "message": "担当者を変更しました",
                "revision": "revision-task-ok",
            },
            {
                "task_id": "task-error",
                "assignee_user_id": "user-b",
                "success": False,
                "changed": False,
                "message": "完了タスクは担当者を変更できません",
            },
        ],
    }

    class FakeService:
        def bulk_update_task_assignees(self, *, operator_user_id, updates):
            assert operator_user_id == "operator"
            assert updates == [
                {"task_id": "task-ok", "assignee_user_id": "user-b"},
                {"task_id": "task-error", "assignee_user_id": "user-b"},
            ]
            return expected

    repository = SimpleNamespace(
        get_task=lambda task_id: SimpleNamespace(revision=f"revision-{task_id}")
    )
    monkeypatch.setattr(main, "task_revision", lambda task: task.revision)
    monkeypatch.setattr(
        main,
        "get_runtime",
        lambda: SimpleNamespace(service=FakeService(), repository=repository),
    )
    body = main.BulkTaskAssigneeBody(
        user_id="operator",
        updates=[
            main.BulkTaskAssigneeRow(task_id="task-ok", assignee_user_id="user-b"),
            main.BulkTaskAssigneeRow(task_id="task-error", assignee_user_id="user-b"),
        ],
    )

    assert main.bulk_update_task_assignees(body) == {"status": "partial", **expected}


def test_bulk_task_assignees_reports_stale_rows_and_keeps_other_updates(monkeypatch) -> None:
    class FakeService:
        def bulk_update_task_assignees(self, *, operator_user_id, updates):
            assert operator_user_id == "operator"
            assert updates == [{"task_id": "task-ok", "assignee_user_id": "user-b"}]
            return {
                "changed": 1,
                "failed": 0,
                "results": [
                    {
                        "task_id": "task-ok",
                        "assignee_user_id": "user-b",
                        "success": True,
                        "changed": True,
                        "message": "担当者を変更しました",
                    }
                ],
            }

    repository = SimpleNamespace(
        get_task=lambda task_id: SimpleNamespace(revision="current-stale" if task_id == "task-stale" else "current-ok")
    )
    monkeypatch.setattr(main, "task_revision", lambda task: task.revision)
    monkeypatch.setattr(
        main,
        "get_runtime",
        lambda: SimpleNamespace(service=FakeService(), repository=repository),
    )

    result = main.bulk_update_task_assignees(
        main.BulkTaskAssigneeBody(
            user_id="operator",
            updates=[
                main.BulkTaskAssigneeRow(
                    task_id="task-stale",
                    assignee_user_id="user-b",
                    expected_revision="old",
                ),
                main.BulkTaskAssigneeRow(
                    task_id="task-ok",
                    assignee_user_id="user-b",
                    expected_revision="current-ok",
                ),
            ],
        )
    )

    assert result["status"] == "partial"
    assert result["changed"] == 1
    assert result["failed"] == 1
    assert result["results"][0]["task_id"] == "task-stale"
    assert result["results"][0]["success"] is False
    assert "別のユーザー" in result["results"][0]["message"]
    assert result["results"][1]["task_id"] == "task-ok"
    assert result["results"][1]["success"] is True
    assert result["results"][1]["revision"] == "current-ok"


def test_bulk_task_assignees_rejects_duplicate_task_rows(monkeypatch) -> None:
    class FakeService:
        def bulk_update_task_assignees(self, *, operator_user_id, updates):
            assert operator_user_id == "operator"
            assert updates == [{"task_id": "task-1", "assignee_user_id": "user-b"}]
            return {
                "changed": 1,
                "failed": 0,
                "results": [
                    {
                        "task_id": "task-1",
                        "assignee_user_id": "user-b",
                        "success": True,
                        "changed": True,
                        "message": "担当者を変更しました",
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
    result = main.bulk_update_task_assignees(
        main.BulkTaskAssigneeBody(
            user_id="operator",
            updates=[
                main.BulkTaskAssigneeRow(task_id="task-1", assignee_user_id="user-b"),
                main.BulkTaskAssigneeRow(task_id="task-1", assignee_user_id="user-c"),
            ],
        )
    )

    assert result["status"] == "partial"
    assert result["changed"] == 1
    assert result["failed"] == 1
    assert result["results"][0]["success"] is True
    assert result["results"][0]["revision"] == "revision-task-1"
    assert result["results"][1]["success"] is False
    assert "重複" in result["results"][1]["message"]
