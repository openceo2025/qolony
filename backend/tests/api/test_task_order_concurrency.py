from __future__ import annotations

from types import SimpleNamespace
from typing import Callable

import pytest

from app import main


class _OrderService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def today_order_revision(self, _user_id: str) -> str:
        return "order-v1-current"

    def reorder_today_tasks(self, _user_id: str, _ordered_task_ids: list[str]) -> str:
        self.calls.append("today")
        return "order-v1-next"

    def wbs_task_sibling_order_revision(self, _task_id: str) -> str:
        return "order-v1-current"

    def move_wbs_task(self, **_kwargs) -> str:
        self.calls.append("move")
        return "order-v1-next"

    def wbs_order_revision(self) -> str:
        return "order-v1-current"

    def reorder_wbs_tasks(self, **_kwargs) -> str:
        self.calls.append("wbs")
        return "order-v1-next"

    def wbs_sibling_order_revision(self, _parent_task_id: str | None) -> str:
        return "order-v1-current"

    def reorder_wbs_sibling_tasks(self, **_kwargs) -> str:
        self.calls.append("siblings")
        return "order-v1-next"


def _order_actions(expected_revision: str | None) -> list[Callable[[], dict[str, str]]]:
    return [
        lambda: main.reorder_today(
            main.TodayReorderBody(
                user_id="USER-1",
                ordered_task_ids=["TASK-2", "TASK-1"],
                expected_order_revision=expected_revision,
            )
        ),
        lambda: main.move_wbs_task(
            "TASK-1",
            main.WbsMoveBody(
                user_id="USER-1",
                direction=1,
                expected_order_revision=expected_revision,
            ),
        ),
        lambda: main.reorder_wbs_tasks(
            main.WbsReorderBody(
                user_id="USER-1",
                ordered_task_ids=["TASK-2", "TASK-1"],
                expected_order_revision=expected_revision,
            )
        ),
        lambda: main.reorder_wbs_sibling_tasks(
            main.WbsSiblingReorderBody(
                user_id="USER-1",
                parent_task_id=None,
                ordered_task_ids=["TASK-2", "TASK-1"],
                expected_order_revision=expected_revision,
            )
        ),
    ]


@pytest.mark.parametrize("expected_revision", [None, "", "order-v1-stale"])
def test_all_order_endpoints_reject_missing_or_stale_revisions(monkeypatch, expected_revision) -> None:
    service = _OrderService()
    monkeypatch.setattr(main, "get_runtime", lambda: SimpleNamespace(service=service))

    for action in _order_actions(expected_revision):
        with pytest.raises(main.TaskConflictError, match="並び順"):
            action()

    assert service.calls == []


def test_all_order_endpoints_accept_the_current_revision(monkeypatch) -> None:
    service = _OrderService()
    monkeypatch.setattr(main, "get_runtime", lambda: SimpleNamespace(service=service))

    results = [action() for action in _order_actions("order-v1-current")]

    assert service.calls == ["today", "move", "wbs", "siblings"]
    assert all(result == {"status": "ok", "order_revision": "order-v1-next"} for result in results)
