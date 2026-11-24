from __future__ import annotations

import json

from datetime import date, datetime, timedelta
from time import perf_counter
from types import SimpleNamespace

from app.tms_core.db.models import Task
from app.tms_core.services.core import TmsService


def _task(task_id: str, *, parent_task_id: str | None, assigned: int) -> Task:
    today = date.today()
    return Task(
        Id=task_id,
        ProjectId="PROJECT-1",
        ParentTaskId=parent_task_id,
        Name=task_id,
        Type="GroupTask" if parent_task_id is None else "Task",
        AssigneeUserId="USER-1",
        CreatorUserId="USER-1",
        StartDate=today,
        EndDate=today + timedelta(days=30),
        AssignedManHour=assigned,
        ActualManHour=0,
        ProgressRate=0.0,
        Status="NotStart",
        Memo="",
        PlannedDeliverablesJson="[]",
        OutputDeliverablesJson="[]",
        IsRework=False,
        PreventedByTaskId=None,
        CompletedAt=None,
        TodayOverride=None,
        TodaySortOrder=None,
        WbsSortOrder=None,
        ChildDependencyMode="Serial",
        IQuavisTaskId=None,
        IQuavisProjectId=None,
        IQuavisLastPulledAt=None,
        IQuavisLastPushedAt=None,
        CreateDate=datetime.now(),
        UpdateDate=datetime.now(),
    )


class _InMemoryCompletionRepository:
    def __init__(self, tasks: list[Task]) -> None:
        self.tasks = {task.Id: task for task in tasks}
        self.batch_call_count = 0

    def get_project(self):
        return SimpleNamespace(Id="PROJECT-1")

    def get_task(self, task_id: str) -> Task | None:
        return self.tasks.get(task_id)

    def list_tasks(self, project_id: str) -> list[Task]:
        assert project_id == "PROJECT-1"
        return list(self.tasks.values())

    def get_state(self, _key: str) -> str | None:
        return None

    def update_task_status(self, task_id: str, status: str, progress_rate: float) -> None:
        task = self.tasks[task_id]
        task.Status = status
        task.ProgressRate = progress_rate

    def update_task_progress_states(self, updates: dict[str, tuple[float, str | None]]) -> None:
        self.batch_call_count += 1
        for task_id, (progress_rate, status) in updates.items():
            task = self.tasks[task_id]
            task.ProgressRate = progress_rate
            if status is not None:
                task.Status = status

    def add_progress_history(self, **_kwargs) -> None:
        return None


def test_complete_task_remains_fast_for_large_wbs() -> None:
    # 実DBの速度に依存せず、全件処理のアルゴリズム退行と行単位更新への逆戻りを検出する。
    leaf_count = 5_000
    tasks = [_task("ROOT", parent_task_id=None, assigned=leaf_count)]
    tasks.extend(
        _task(f"LEAF-{index}", parent_task_id="ROOT", assigned=1)
        for index in range(leaf_count)
    )
    repository = _InMemoryCompletionRepository(tasks)
    service = TmsService(settings=SimpleNamespace(), repository=repository)  # type: ignore[arg-type]

    started_at = perf_counter()
    service.complete_task("LEAF-0")
    elapsed_seconds = perf_counter() - started_at

    assert repository.batch_call_count == 1
    assert repository.tasks["LEAF-0"].Status == "Completion"
    assert elapsed_seconds < 2.0


def test_complete_task_remains_fast_when_all_estimates_are_zero() -> None:
    # 工数が全て0でも、葉判定が全件ごとの集合再構築に戻らないことを保証する。
    leaf_count = 5_000
    tasks = [_task("ROOT", parent_task_id=None, assigned=0)]
    tasks.extend(
        _task(f"LEAF-{index}", parent_task_id="ROOT", assigned=0)
        for index in range(leaf_count)
    )
    repository = _InMemoryCompletionRepository(tasks)
    service = TmsService(settings=SimpleNamespace(), repository=repository)  # type: ignore[arg-type]

    started_at = perf_counter()
    service.complete_task("LEAF-0")
    elapsed_seconds = perf_counter() - started_at

    assert repository.batch_call_count == 1
    assert repository.tasks["LEAF-0"].Status == "Completion"
    assert elapsed_seconds < 2.0


class _CountingCompletionRepository(_InMemoryCompletionRepository):
    """全件読み込みと親子表の再構築が何回起きたかを数える。"""

    def __init__(self, tasks: list[Task], excluded_root_ids: list[str]) -> None:
        super().__init__(tasks)
        self.list_tasks_call_count = 0
        self.enqueued_task_ids: list[str] = []
        self._excluded_root_ids = excluded_root_ids

    def list_tasks(self, project_id: str) -> list[Task]:
        self.list_tasks_call_count += 1
        return super().list_tasks(project_id)

    def get_state(self, key: str) -> str | None:
        if key == "IQuavisSyncExcludedRootTaskIdsV1":
            return json.dumps(self._excluded_root_ids)
        return None

    def enqueue_iquavis_sync_job(self, *, task_id: str, **_kwargs) -> None:
        self.enqueued_task_ids.append(task_id)


def _service_with_excluded_roots(root_count: int, leaves_per_root: int):
    tasks: list[Task] = []
    excluded_root_ids: list[str] = []
    for root_index in range(root_count):
        root_id = f"ROOT-{root_index}"
        tasks.append(_task(root_id, parent_task_id=None, assigned=leaves_per_root))
        excluded_root_ids.append(root_id)
        for leaf_index in range(leaves_per_root):
            tasks.append(_task(f"{root_id}-LEAF-{leaf_index}", parent_task_id=root_id, assigned=1))
    # 同期対象として残す、除外されていないタスク。
    tasks.append(_task("LIVE-ROOT", parent_task_id=None, assigned=1))
    tasks.append(_task("LIVE-LEAF", parent_task_id="LIVE-ROOT", assigned=1))
    repository = _CountingCompletionRepository(tasks, excluded_root_ids)
    service = TmsService(settings=SimpleNamespace(), repository=repository)  # type: ignore[arg-type]
    return service, repository


def test_completion_builds_the_child_index_once_for_all_excluded_roots(monkeypatch) -> None:
    # 除外ルートごとに親子表を作り直すと、ルート数×全タスク数の走査になり完了が1秒級まで落ちる。
    service, repository = _service_with_excluded_roots(root_count=60, leaves_per_root=30)
    build_count = 0
    original = TmsService._task_children_by_parent

    def counted(self, tasks):
        nonlocal build_count
        build_count += 1
        return original(self, tasks)

    monkeypatch.setattr(TmsService, "_task_children_by_parent", counted)

    service.complete_task("LIVE-LEAF")

    # 集計用と除外判定用で数回作るのは許すが、ルート数に比例して増えてはいけない。
    assert build_count <= 4, f"child index rebuilt {build_count} times for 60 excluded roots"


def test_completion_reads_all_tasks_once(monkeypatch) -> None:
    # 再計算と同期キュー投入で全件読み込みを2回やると、1万件規模で数百ミリ秒を捨てる。
    service, repository = _service_with_excluded_roots(root_count=5, leaves_per_root=5)

    service.complete_task("LIVE-LEAF")

    assert repository.list_tasks_call_count == 1


def test_completion_still_skips_excluded_tasks_when_queueing(monkeypatch) -> None:
    # 速度改善で除外判定が緩んでいないことを確かめる。
    service, repository = _service_with_excluded_roots(root_count=3, leaves_per_root=3)
    repository.tasks["ROOT-0-LEAF-0"].IQuavisTaskId = "IQ-EXCLUDED"
    repository.tasks["LIVE-LEAF"].IQuavisTaskId = "IQ-LIVE"

    service.complete_task("LIVE-LEAF")

    assert "IQ-EXCLUDED" not in repository.enqueued_task_ids
    assert repository.enqueued_task_ids == ["LIVE-LEAF"]
