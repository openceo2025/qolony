from __future__ import annotations

from datetime import date, datetime
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from app.main import TaskConflictError, _guard_task_revision, _require_order_revision
from app.tms_core.services.core import task_order_revision, task_revision


def _task(**overrides):
    values = {
        "Id": "TASK-1",
        "ParentTaskId": None,
        "Name": "Task",
        "Type": "Task",
        "AssigneeUserId": "USER-1",
        "StartDate": date(2026, 7, 1),
        "EndDate": date(2026, 7, 31),
        "AssignedManHour": 60,
        "ActualManHour": 0,
        "ProgressRate": 0.0,
        "Status": "NotStart",
        "Memo": "before",
        "PlannedDeliverablesJson": "[]",
        "OutputDeliverablesJson": "[]",
        "TodayOverride": None,
        "TodaySortOrder": None,
        "WbsSortOrder": None,
        "ChildDependencyMode": "Serial",
        "UpdateDate": datetime(2026, 7, 20, 10, 0, 0),
        "UpdatedByUserId": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_task_revision_changes_when_user_editable_content_changes() -> None:
    before = _task()
    after = _task(Memo="after")
    assert task_revision(before) != task_revision(after)


def test_task_revision_ignores_display_order_changes() -> None:
    before = _task(TodaySortOrder=1, WbsSortOrder=2)
    after = _task(TodaySortOrder=9, WbsSortOrder=8)
    assert task_revision(before) == task_revision(after)


def test_task_order_revision_is_deterministic_and_order_sensitive() -> None:
    assert task_order_revision([]) == "order-v1-09612b07b5ecb5a5"
    assert task_order_revision(["TASK-A", "TASK-B"]) == "order-v1-891b4fcb344dc422"
    assert task_order_revision(["TASK-B", "TASK-A"]) == "order-v1-f1e35e3943facb74"


def test_order_revision_guard_rejects_a_stale_order() -> None:
    _require_order_revision("order-v1-current", "order-v1-current")

    for expected_revision in (None, "", "order-v1-stale"):
        with pytest.raises(TaskConflictError, match="並び順"):
            _require_order_revision(expected_revision, "order-v1-current")


def test_task_revision_guard_rejects_a_stale_multi_user_edit() -> None:
    current = _task()
    repository = SimpleNamespace(
        get_task=lambda _task_id: current,
        get_user=lambda _user_id: None,
    )
    runtime = SimpleNamespace(repository=repository)
    expected = task_revision(current)

    with _guard_task_revision(runtime, current.Id, expected):
        pass

    current.Memo = "updated by another user"
    with pytest.raises(TaskConflictError, match="別のユーザー"):
        with _guard_task_revision(runtime, current.Id, expected):
            pass


def test_task_revision_guards_serialize_different_tasks_with_shared_ancestor_writes() -> None:
    tasks = {
        "TASK-A": _task(Id="TASK-A"),
        "TASK-B": _task(Id="TASK-B"),
    }
    runtime = SimpleNamespace(repository=SimpleNamespace(get_task=lambda task_id: tasks.get(task_id)))
    first_entered = Event()
    release_first = Event()
    second_entered = Event()

    def hold_first_guard() -> None:
        with _guard_task_revision(runtime, "TASK-A", task_revision(tasks["TASK-A"])):
            first_entered.set()
            release_first.wait(timeout=2)

    def enter_second_guard() -> None:
        first_entered.wait(timeout=2)
        with _guard_task_revision(runtime, "TASK-B", task_revision(tasks["TASK-B"])):
            second_entered.set()

    first = Thread(target=hold_first_guard)
    second = Thread(target=enter_second_guard)
    first.start()
    second.start()
    assert first_entered.wait(timeout=1)
    assert not second_entered.wait(timeout=0.05)
    release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)
    assert second_entered.is_set()


def test_task_revision_guard_records_who_changed_the_task() -> None:
    # 競合時に「誰の変更とぶつかったか」を出すため、成功した更新の操作者を残す。
    current = _task()
    recorded: list[tuple[str, str | None]] = []
    repository = SimpleNamespace(
        get_task=lambda _task_id: current,
        mark_task_updated_by=lambda task_id, user_id: recorded.append((task_id, user_id)),
    )
    runtime = SimpleNamespace(repository=repository)

    with _guard_task_revision(
        runtime,
        current.Id,
        task_revision(current),
        operator_user_id="USER-2",
    ):
        pass

    assert recorded == [("TASK-1", "USER-2")]


def test_task_conflict_carries_the_other_side_current_values() -> None:
    # 利用者どうしが話して解決できるよう、相手の現在値と更新者を添えて返す。
    current = _task()
    other = SimpleNamespace(Id="USER-9", Name="鈴木")
    repository = SimpleNamespace(
        get_task=lambda _task_id: current,
        get_user=lambda user_id: other if user_id == "USER-9" else None,
        mark_task_updated_by=lambda _task_id, _user_id: None,
    )
    runtime = SimpleNamespace(repository=repository)
    expected = task_revision(current)

    current.Memo = "他の人が書いたメモ"
    current.UpdatedByUserId = "USER-9"
    with pytest.raises(TaskConflictError) as excinfo:
        with _guard_task_revision(runtime, current.Id, expected, operator_user_id="USER-1"):
            pass

    conflict = excinfo.value.conflict
    assert conflict is not None
    assert conflict["updated_by_user_name"] == "鈴木"
    assert conflict["server"]["Memo"] == "他の人が書いたメモ"
    assert conflict["current_revision"] == task_revision(current)
