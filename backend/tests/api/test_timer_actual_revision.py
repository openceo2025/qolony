from __future__ import annotations

from types import SimpleNamespace

from app import main


def test_add_timer_actual_returns_post_update_task_revision(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeService:
        def record_timer_interval(self, **kwargs) -> None:
            calls.append(kwargs)

    repository = SimpleNamespace(
        get_task=lambda task_id: SimpleNamespace(Id=task_id, revision="revision-after-actual")
    )
    monkeypatch.setattr(main, "task_revision", lambda task: task.revision)
    monkeypatch.setattr(
        main,
        "get_runtime",
        lambda: SimpleNamespace(service=FakeService(), repository=repository),
    )

    result = main.add_timer_actual(
        main.TimerAddActualBody(
            user_id="user-1",
            task_id="task-1",
            started_at_ms=1_000,
            ended_at_ms=61_000,
            is_ojt=False,
            teacher_user_id=None,
        )
    )

    assert calls == [
        {
            "user_id": "user-1",
            "task_id": "task-1",
            "started_at_ms": 1_000,
            "ended_at_ms": 61_000,
            "is_ojt": False,
            "teacher_user_id": None,
        }
    ]
    assert result == {
        "status": "ok",
        "revision": "revision-after-actual",
        "warnings": [],
    }
