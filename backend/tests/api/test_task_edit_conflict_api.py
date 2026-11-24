from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import main
from app.tms_core.services.core import task_revision


def _task(**overrides):
    values = {
        "Id": "TASK-1",
        "ParentTaskId": None,
        "Name": "休暇",
        "Type": "Task",
        "AssigneeUserId": "USER-1",
        "StartDate": date(2026, 8, 1),
        "EndDate": date(2026, 8, 2),
        "AssignedManHour": 480,
        "ActualManHour": 0,
        "ProgressRate": 0.0,
        "Status": "NotStart",
        "Memo": "",
        "PlannedDeliverablesJson": "[]",
        "OutputDeliverablesJson": "[]",
        "TodayOverride": True,
        "TodaySortOrder": 1,
        "WbsSortOrder": 1,
        "ChildDependencyMode": "Serial",
        "UpdateDate": datetime(2026, 8, 5, 10, 0, 0),
        "UpdatedByUserId": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _runtime(current, users):
    repository = SimpleNamespace(
        get_task=lambda _task_id: current,
        get_user=lambda user_id: users.get(user_id),
        mark_task_updated_by=lambda _task_id, _user_id: None,
    )
    return SimpleNamespace(
        repository=repository,
        service=SimpleNamespace(complete_task=lambda *_args, **_kwargs: None),
    )


def test_conflicting_complete_returns_who_changed_it_and_their_values(monkeypatch) -> None:
    current = _task()
    stale_revision = task_revision(current)
    # 別のユーザーが先に中止へ変えた状態を作る。
    current.Status = "Abort"
    current.UpdatedByUserId = "USER-9"
    users = {"USER-9": SimpleNamespace(Id="USER-9", Name="鈴木"), "USER-1": SimpleNamespace(Id="USER-1", Name="山田")}
    monkeypatch.setattr(main, "get_runtime", lambda: _runtime(current, users))

    response = TestClient(main.app).post(
        "/api/tasks/TASK-1/complete",
        json={"user_id": "USER-1", "expected_revision": stale_revision},
    )

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "task_conflict"
    conflict = body["conflict"]
    assert conflict["updated_by_user_name"] == "鈴木"
    assert conflict["server"]["Status"] == "Abort"
    assert conflict["task_name"] == "休暇"
    # 相手の値で再送できるよう、いまのリビジョンも返す。
    assert conflict["current_revision"] == task_revision(current)


def test_force_save_without_expected_revision_overwrites_the_other_change(monkeypatch) -> None:
    # 話し合って自分の変更を通すと決めたときは、リビジョン照合を外して先着を上書きする。
    current = _task(Status="Abort", UpdatedByUserId="USER-9")
    completed: list[str] = []
    runtime = _runtime(current, {"USER-9": SimpleNamespace(Id="USER-9", Name="鈴木")})
    runtime.service = SimpleNamespace(
        complete_task=lambda task_id, operator_user_id=None: completed.append(task_id)
    )
    monkeypatch.setattr(main, "get_runtime", lambda: runtime)

    response = TestClient(main.app).post(
        "/api/tasks/TASK-1/complete",
        json={"user_id": "USER-1"},
    )

    assert response.status_code == 200
    assert completed == ["TASK-1"]
