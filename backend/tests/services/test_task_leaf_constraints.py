from datetime import date, datetime, timedelta
from time import perf_counter

import pytest

from app.tms_core.config import (
    AppConfig,
    CalendarConfig,
    IQuavisConfig,
    Settings,
    TeamConfig,
    UsersConfig,
)
from app.tms_core.db.models import Task
from app.tms_core.db.repository import NewTaskInput, TmsRepository
from app.tms_core.db.session import create_all_tables, create_session_factory, create_sqlite_engine
from app.tms_core.services.core import TmsService, UserInputError


def _build_service(tmp_path):
    today = date.today()
    project_start = today - timedelta(days=7)
    project_end = today + timedelta(days=365)
    task_end = today + timedelta(days=30)

    db_path = tmp_path / "leaf_constraints.db"
    engine = create_sqlite_engine(db_path)
    create_all_tables(engine)
    repository = TmsRepository(create_session_factory(engine))

    repository.ensure_users(
        names=["山田", "高橋"],
        top_manager_name="山田",
        free_time_rate_default=0.2,
    )
    project = repository.ensure_single_project(
        name="QOLONY",
        start_date=project_start,
        end_date=project_end,
        crash_zone_pct=20.0,
    )

    top = repository.get_user_by_name("山田")
    member = repository.get_user_by_name("高橋")
    assert top is not None and member is not None

    parent = repository.create_task(
        NewTaskInput(
            ProjectId=project.Id,
            ParentTaskId=None,
            Name="親タスク",
            Type="GroupTask",
            AssigneeUserId=member.Id,
            CreatorUserId=top.Id,
            StartDate=today,
            EndDate=task_end,
            AssignedManHour=600,
            PlannedDeliverables=[],
        )
    )
    child = repository.create_task(
        NewTaskInput(
            ProjectId=project.Id,
            ParentTaskId=parent.Id,
            Name="子タスク",
            Type="Task",
            AssigneeUserId=member.Id,
            CreatorUserId=top.Id,
            StartDate=today,
            EndDate=task_end,
            AssignedManHour=300,
            PlannedDeliverables=[],
        )
    )
    child2 = repository.create_task(
        NewTaskInput(
            ProjectId=project.Id,
            ParentTaskId=parent.Id,
            Name="子タスク2",
            Type="Task",
            AssigneeUserId=member.Id,
            CreatorUserId=top.Id,
            StartDate=today,
            EndDate=task_end,
            AssignedManHour=300,
            PlannedDeliverables=[],
        )
    )

    settings = Settings(
        app=AppConfig(
            db_path=db_path,
            crash_zone_pct=20.0,
            task_signal_capacity_minutes_per_day=240,
            seed_initial_tasks_on_empty_db=False,
            seed_demo_data_on_empty_db=False,
            free_time_rate_default=0.2,
            regular_minutes_per_day=480,
            mobile_window_width=460,
            mobile_window_height=800,
            daily_snapshot_hour=0,
            daily_snapshot_minute=5,
        ),
        iquavis=IQuavisConfig(
            enabled=False,
            base_url="",
            username="",
            password="",
            timeout_seconds=10.0,
            max_retries=5,
            retry_base_seconds=30,
            poll_interval_seconds=60,
            allowed_project_ids=[],
        ),
        team=TeamConfig(
            project_name="QOLONY",
            project_start_date=project_start,
            project_end_date=project_end,
            default_education_rate_monthly=0.1,
        ),
        users=UsersConfig(top_manager="山田", members=["山田", "高橋"]),
        calendar=CalendarConfig(holidays=set()),
        initial_tasks=[],
        split_templates=[],
    )
    service = TmsService(settings=settings, repository=repository)
    service.recompute_all_task_progress()
    return service, repository, member.Id, parent.Id, child.Id, child2.Id


def test_today_toggle_rejects_non_leaf_task(tmp_path) -> None:
    service, _, member_id, parent_id, _, _ = _build_service(tmp_path)
    with pytest.raises(UserInputError):
        service.set_today_toggle(member_id, parent_id, True)


def test_today_list_excludes_non_leaf_task(tmp_path) -> None:
    service, repository, member_id, parent_id, child_id, _ = _build_service(tmp_path)
    repository.update_task_today_override(parent_id, True)
    service.set_today_toggle(member_id, child_id, True)
    today_ids = {task.Id for task in service.list_today_tasks(member_id)}
    assert parent_id not in today_ids
    assert child_id in today_ids


def test_move_wbs_task_reorders_only_same_parent_siblings(tmp_path) -> None:
    service, repository, member_id, parent_id, child_id, child2_id = _build_service(tmp_path)

    service.move_wbs_task(member_id, child2_id, -1)

    child = repository.get_task(child_id)
    child2 = repository.get_task(child2_id)
    parent = repository.get_task(parent_id)
    assert child is not None and child2 is not None and parent is not None
    assert child2.WbsSortOrder == 1
    assert child.WbsSortOrder == 2
    assert parent.WbsSortOrder is None

    service.move_wbs_task(member_id, child2_id, -1)

    child = repository.get_task(child_id)
    child2 = repository.get_task(child2_id)
    assert child is not None and child2 is not None
    assert child2.WbsSortOrder == 1
    assert child.WbsSortOrder == 2


def test_reorder_wbs_tasks_persists_sibling_order_from_full_tree_order(tmp_path) -> None:
    service, repository, member_id, parent_id, child_id, child2_id = _build_service(tmp_path)

    service.reorder_wbs_tasks(
        member_id,
        ordered_task_ids=[parent_id, child2_id, child_id],
    )

    child = repository.get_task(child_id)
    child2 = repository.get_task(child2_id)
    parent = repository.get_task(parent_id)
    assert child is not None and child2 is not None and parent is not None
    assert child2.WbsSortOrder == 1
    assert child.WbsSortOrder == 2
    assert parent.WbsSortOrder == 1


def test_reorder_wbs_sibling_tasks_persists_only_selected_siblings(tmp_path) -> None:
    service, repository, member_id, parent_id, child_id, child2_id = _build_service(tmp_path)

    service.reorder_wbs_sibling_tasks(
        operator_user_id=member_id,
        parent_task_id=parent_id,
        ordered_task_ids=[child2_id, child_id],
    )

    child = repository.get_task(child_id)
    child2 = repository.get_task(child2_id)
    parent = repository.get_task(parent_id)
    assert child is not None and child2 is not None and parent is not None
    assert child2.WbsSortOrder == 1
    assert child.WbsSortOrder == 2
    assert parent.WbsSortOrder is None


def test_start_timer_rejects_non_leaf_task(tmp_path) -> None:
    service, _, member_id, parent_id, _, _ = _build_service(tmp_path)
    with pytest.raises(UserInputError):
        service.start_timer(
            member_id,
            parent_id,
            is_ojt=False,
            teacher_user_id=None,
        )


def test_record_timer_interval_rejects_non_leaf_task(tmp_path) -> None:
    service, _, member_id, parent_id, _, _ = _build_service(tmp_path)
    start_at = datetime.now() - timedelta(hours=2)
    end_at = datetime.now()
    with pytest.raises(UserInputError):
        service.record_timer_interval(
            user_id=member_id,
            task_id=parent_id,
            started_at_ms=int(start_at.timestamp() * 1000),
            ended_at_ms=int(end_at.timestamp() * 1000),
            is_ojt=False,
            teacher_user_id=None,
        )


def test_set_task_actual_manhour_rejects_non_leaf_task(tmp_path) -> None:
    service, _, member_id, parent_id, _, _ = _build_service(tmp_path)
    with pytest.raises(UserInputError):
        service.set_task_actual_manhour(parent_id, 30, operator_user_id=member_id)


def test_completed_task_rejects_edit_and_split(tmp_path) -> None:
    service, _, member_id, _, child_id, _ = _build_service(tmp_path)
    service.complete_task(child_id)

    with pytest.raises(UserInputError):
        service.set_task_memo(child_id, "変更不可")

    with pytest.raises(UserInputError):
        service.set_task_actual_manhour(child_id, 60, operator_user_id=member_id)

    with pytest.raises(UserInputError):
        service.add_output_deliverable(child_id, "x", "https://example.com/x")

    with pytest.raises(UserInputError):
        service.split_task(
            parent_task_id=child_id,
            operator_user_id=member_id,
            children=[
                {
                    "Name": "子1",
                    "AssignedManHour": 300,
                    "EndDate": (date.today() + timedelta(days=30)).isoformat(),
                    "Memo": "",
                }
            ],
        )


def test_parent_auto_completes_when_all_children_completed(tmp_path) -> None:
    service, repository, member_id, parent_id, child_id, child2_id = _build_service(tmp_path)

    service.complete_task(child_id, operator_user_id=member_id)
    service.complete_task(child2_id, operator_user_id=member_id)

    parent = repository.get_task(parent_id)
    assert parent is not None
    assert parent.Status == "Completion"
    assert parent.ProgressRate == pytest.approx(100.0)


def test_complete_task_batches_rollup_updates_and_reuses_progress_aggregate(tmp_path, monkeypatch) -> None:
    service, repository, member_id, parent_id, child_id, _ = _build_service(tmp_path)
    original_batch_update = repository.update_task_progress_states
    batch_calls: list[dict[str, tuple[float, str | None]]] = []

    def record_batch(updates: dict[str, tuple[float, str | None]]) -> None:
        batch_calls.append(dict(updates))
        original_batch_update(updates)

    def reject_per_row_update(*_args, **_kwargs) -> None:
        pytest.fail("完了時の進捗更新が行単位トランザクションへ戻っています")

    def reject_second_aggregate() -> float:
        pytest.fail("完了時にプロジェクト進捗を二重集計しています")

    monkeypatch.setattr(repository, "update_task_progress_states", record_batch)
    monkeypatch.setattr(repository, "update_task_progress_rate", reject_per_row_update)
    monkeypatch.setattr(service, "project_progress_rate", reject_second_aggregate)

    service.complete_task(child_id, operator_user_id=member_id)

    assert len(batch_calls) == 1
    assert parent_id in batch_calls[0]


def test_complete_task_is_fast_with_many_sqlite_rollup_updates(tmp_path) -> None:
    service, repository, member_id, _, child_id, _ = _build_service(tmp_path)
    project = service.get_project()
    now = datetime.now()
    today = date.today()
    extra_tasks: list[Task] = []

    for index in range(800):
        parent_id = f"perf-parent-{index:04d}"
        extra_tasks.append(
            Task(
                Id=parent_id,
                ProjectId=project.Id,
                ParentTaskId=None,
                Name=parent_id,
                Type="GroupTask",
                AssigneeUserId=member_id,
                CreatorUserId=member_id,
                StartDate=today,
                EndDate=today + timedelta(days=30),
                AssignedManHour=30,
                ActualManHour=0,
                ProgressRate=0.0,
                Status="NotStart",
                Memo="",
                PlannedDeliverablesJson="[]",
                OutputDeliverablesJson="[]",
                IsRework=False,
                ChildDependencyMode="Serial",
                CreateDate=now,
                UpdateDate=now,
            )
        )
        extra_tasks.append(
            Task(
                Id=f"perf-child-{index:04d}",
                ProjectId=project.Id,
                ParentTaskId=parent_id,
                Name=f"perf-child-{index:04d}",
                Type="Task",
                AssigneeUserId=member_id,
                CreatorUserId=member_id,
                StartDate=today,
                EndDate=today + timedelta(days=30),
                AssignedManHour=30,
                ActualManHour=0,
                ProgressRate=100.0,
                Status="Completion",
                Memo="",
                PlannedDeliverablesJson="[]",
                OutputDeliverablesJson="[]",
                IsRework=False,
                ChildDependencyMode="Serial",
                CreateDate=now,
                UpdateDate=now,
            )
        )

    # テスト準備は一括挿入し、完了操作そのもののSQLite処理時間だけを測る。
    with repository._session() as session:
        session.add_all(extra_tasks)

    started_at = perf_counter()
    service.complete_task(child_id, operator_user_id=member_id)
    elapsed_seconds = perf_counter() - started_at

    assert elapsed_seconds < 2.0


def test_parent_reopens_when_resplit_adds_incomplete_child(tmp_path) -> None:
    service, repository, member_id, parent_id, child_id, child2_id = _build_service(tmp_path)

    service.complete_task(child_id, operator_user_id=member_id)
    service.complete_task(child2_id, operator_user_id=member_id)

    parent_before = repository.get_task(parent_id)
    assert parent_before is not None
    assert parent_before.Status == "Completion"

    due = (date.today() + timedelta(days=10)).isoformat()
    service.split_task(
        parent_task_id=parent_id,
        operator_user_id=member_id,
        children=[
            {
                "ExistingTaskId": child_id,
                "Name": "既存1",
                "AssignedManHour": 250,
                "ActualManHour": 0,
                "EndDate": due,
                "Memo": "",
            },
            {
                "ExistingTaskId": child2_id,
                "Name": "既存2",
                "AssignedManHour": 250,
                "ActualManHour": 0,
                "EndDate": due,
                "Memo": "",
            },
            {
                "Name": "新規未完了",
                "AssignedManHour": 100,
                "ActualManHour": 0,
                "EndDate": due,
                "Memo": "",
                "AssigneeUserId": member_id,
            },
        ],
    )

    parent_after = repository.get_task(parent_id)
    assert parent_after is not None
    assert parent_after.Status != "Completion"
    assert parent_after.ProgressRate < 100.0


def test_create_task_under_completed_parent_is_blocked(tmp_path) -> None:
    service, repository, member_id, _, child_id, _ = _build_service(tmp_path)
    service.complete_task(child_id)
    top = repository.get_user_by_name("山田")
    assert top is not None

    with pytest.raises(UserInputError):
        service.create_task(
            name="完了配下禁止",
            assignee_user_id=member_id,
            creator_user_id=top.Id,
            assigned_manhour=300,
            end_date=date.today() + timedelta(days=30),
            parent_task_id=child_id,
            planned_deliverables=[],
            is_rework=False,
            memo="",
        )


def test_member_cannot_create_root_task(tmp_path) -> None:
    service, _, member_id, _, _, _ = _build_service(tmp_path)
    with pytest.raises(UserInputError):
        service.create_task(
            name="root by member",
            assignee_user_id=member_id,
            creator_user_id=member_id,
            assigned_manhour=300,
            end_date=date.today() + timedelta(days=30),
            parent_task_id=None,
            planned_deliverables=[],
            is_rework=False,
            memo="",
        )


def test_switch_timer_without_pause_or_done_pauses_previous_timer(tmp_path) -> None:
    service, repository, member_id, _, child_id, child2_id = _build_service(tmp_path)

    service.start_timer(
        member_id,
        child_id,
        is_ojt=False,
        teacher_user_id=None,
    )
    service.start_timer(
        member_id,
        child2_id,
        is_ojt=False,
        teacher_user_id=None,
    )

    first_timers = repository.list_timers_by_task(child_id)
    assert len(first_timers) == 1
    assert first_timers[0].Status == "Paused"
    assert first_timers[0].AutoStopReason == "ReplacedByNewTimer"

    first_task = repository.get_task(child_id)
    assert first_task is not None
    assert first_task.ActualManHour == 0


def test_record_timer_interval_updates_task_actual_manhour(tmp_path) -> None:
    service, repository, member_id, _, child_id, _ = _build_service(tmp_path)

    end_at = datetime.now()
    start_at = end_at - timedelta(hours=2)
    service.record_timer_interval(
        user_id=member_id,
        task_id=child_id,
        started_at_ms=int(start_at.timestamp() * 1000),
        ended_at_ms=int(end_at.timestamp() * 1000),
        is_ojt=False,
        teacher_user_id=None,
    )

    child = repository.get_task(child_id)
    assert child is not None
    assert child.ActualManHour == 120


def test_record_timer_interval_is_idempotent_for_repeated_exit_delivery(tmp_path) -> None:
    service, repository, member_id, _, child_id, _ = _build_service(tmp_path)

    end_at = datetime.now()
    start_at = end_at - timedelta(hours=2)
    payload = {
        "user_id": member_id,
        "task_id": child_id,
        "started_at_ms": int(start_at.timestamp() * 1000),
        "ended_at_ms": int(end_at.timestamp() * 1000),
        "is_ojt": False,
        "teacher_user_id": None,
    }

    service.record_timer_interval(**payload)
    service.record_timer_interval(**payload)

    child = repository.get_task(child_id)
    assert child is not None
    assert child.ActualManHour == 120
    worklogs = repository.list_raw_worklogs_by_task(child_id)
    # 日跨ぎ区間は日次集計のため0時で分割される。再送でその行数が
    # 倍増していないことを、元区間に必要なチャンク数と比較する。
    expected_chunks = 1 + (end_at.date() - start_at.date()).days
    assert len(worklogs) == expected_chunks
    assert sum(item.DurationSeconds for item in worklogs) == pytest.approx(2 * 60 * 60)


def test_split_task_allows_one_minute_allocation(tmp_path) -> None:
    service, repository, member_id, _, child_id, _ = _build_service(tmp_path)
    child = repository.get_task(child_id)
    assert child is not None

    service.split_task(
        parent_task_id=child_id,
        operator_user_id=member_id,
        children=[
            {
                "Name": "1分タスク",
                "AssignedManHour": 1,
                "EndDate": child.EndDate.isoformat(),
                "Memo": "",
            },
            {
                "Name": "299分タスク",
                "AssignedManHour": 299,
                "EndDate": child.EndDate.isoformat(),
                "Memo": "",
            },
        ],
    )

    created = [task for task in repository.list_tasks(child.ProjectId) if task.ParentTaskId == child_id]
    assert sorted(task.AssignedManHour for task in created) == [1, 299]


def test_update_task_assignee_and_end_date(tmp_path) -> None:
    service, repository, _, parent_id, child_id, _ = _build_service(tmp_path)
    top = repository.get_user_by_name("山田")
    parent = repository.get_task(parent_id)
    assert top is not None
    assert parent is not None

    next_due = parent.EndDate - timedelta(days=1)
    service.update_task_assignee_and_end_date(
        task_id=child_id,
        assignee_user_id=top.Id,
        end_date=next_due,
    )

    updated = repository.get_task(child_id)
    assert updated is not None
    assert updated.AssigneeUserId == top.Id
    assert updated.EndDate == next_due


def test_update_task_assignee_blocks_leaf_with_actual_manhour(tmp_path) -> None:
    service, repository, member_id, _, child_id, _ = _build_service(tmp_path)
    top = repository.get_top_manager()
    child = repository.get_task(child_id)
    assert top is not None
    assert child is not None

    repository.update_task_actual_manhour(child_id, 30)

    with pytest.raises(UserInputError):
        service.update_task_assignee_and_end_date(
            task_id=child_id,
            assignee_user_id=top.Id,
            end_date=child.EndDate,
            operator_user_id=member_id,
        )


def test_update_task_assignee_allows_parent_task(tmp_path) -> None:
    service, repository, member_id, parent_id, _, _ = _build_service(tmp_path)
    top = repository.get_top_manager()
    assert top is not None

    service.update_task_assignee(
        task_id=parent_id,
        assignee_user_id=top.Id,
        operator_user_id=member_id,
    )

    updated = repository.get_task(parent_id)
    assert updated is not None
    assert updated.AssigneeUserId == top.Id


def test_bulk_update_task_assignees_reports_non_assignee_non_top_per_row(tmp_path) -> None:
    service, repository, _, _, child_id, _ = _build_service(tmp_path)
    top = repository.get_top_manager()
    assert top is not None

    existing_names = [user.Name for user in repository.list_users()]
    repository.ensure_users(
        names=[*existing_names, "visitor"],
        top_manager_name=top.Name,
        free_time_rate_default=0.2,
    )
    visitor = repository.get_user_by_name("visitor")
    assert visitor is not None

    result = service.bulk_update_task_assignees(
        operator_user_id=visitor.Id,
        updates=[{"task_id": child_id, "assignee_user_id": top.Id}],
    )

    assert result["changed"] == 0
    assert result["failed"] == 1
    assert result["results"][0]["task_id"] == child_id
    assert result["results"][0]["success"] is False
    assert "担当" in result["results"][0]["message"]


def test_bulk_update_task_assignees_allows_direct_parent_owner(tmp_path) -> None:
    service, repository, member_id, parent_id, child_id, _ = _build_service(tmp_path)
    top = repository.get_top_manager()
    parent = repository.get_task(parent_id)
    child = repository.get_task(child_id)
    assert top is not None
    assert parent is not None
    assert child is not None

    repository.update_task_assignee_and_end_date(child_id, top.Id, child.EndDate)

    result = service.bulk_update_task_assignees(
        operator_user_id=member_id,
        updates=[{"task_id": child_id, "assignee_user_id": member_id}],
    )

    updated_child = repository.get_task(child_id)
    assert updated_child is not None
    assert result["changed"] == 1
    assert result["failed"] == 0
    assert result["results"][0]["success"] is True
    assert updated_child.AssigneeUserId == member_id


def test_bulk_update_task_assignees_reports_leaf_actual_error_per_row(tmp_path) -> None:
    service, repository, member_id, _, child_id, _ = _build_service(tmp_path)
    top = repository.get_top_manager()
    child = repository.get_task(child_id)
    assert top is not None
    assert child is not None

    repository.update_task_assignee_and_end_date(child_id, top.Id, child.EndDate)
    repository.update_task_actual_manhour(child_id, 30)

    result = service.bulk_update_task_assignees(
        operator_user_id=member_id,
        updates=[{"task_id": child_id, "assignee_user_id": member_id}],
    )

    assert result["changed"] == 0
    assert result["failed"] == 1
    assert result["results"][0]["success"] is False
    assert "実績MHが0" in result["results"][0]["message"]


def test_bulk_update_task_assignees_keeps_successes_when_other_rows_fail(tmp_path) -> None:
    service, repository, member_id, _, child_id, child2_id = _build_service(tmp_path)
    top = repository.get_top_manager()
    assert top is not None

    repository.update_task_actual_manhour(child2_id, 30)

    result = service.bulk_update_task_assignees(
        operator_user_id=member_id,
        updates=[
            {"task_id": child_id, "assignee_user_id": top.Id},
            {"task_id": child2_id, "assignee_user_id": top.Id},
            {"task_id": "missing-task", "assignee_user_id": top.Id},
        ],
    )

    updated_child = repository.get_task(child_id)
    unchanged_child = repository.get_task(child2_id)
    assert updated_child is not None
    assert unchanged_child is not None
    assert updated_child.AssigneeUserId == top.Id
    assert unchanged_child.AssigneeUserId == member_id
    assert result["changed"] == 1
    assert result["failed"] == 2
    assert [row["success"] for row in result["results"]] == [True, False, False]
    assert "実績MHが0" in result["results"][1]["message"]
    assert result["results"][2]["message"] == "タスクが見つかりません"


def test_update_task_assignee_and_end_date_expands_parent_for_later_due_date(tmp_path) -> None:
    service, repository, member_id, parent_id, child_id, _ = _build_service(tmp_path)
    parent = repository.get_task(parent_id)
    assert parent is not None

    next_due = parent.EndDate + timedelta(days=1)
    warnings = service.update_task_assignee_and_end_date(
        task_id=child_id,
        assignee_user_id=member_id,
        end_date=next_due,
    )

    updated_parent = repository.get_task(parent_id)
    updated_child = repository.get_task(child_id)
    assert updated_parent is not None and updated_child is not None
    assert updated_parent.EndDate == next_due
    assert updated_child.EndDate == next_due
    assert any("自動調整" in warning for warning in warnings)


def test_top_manager_can_split_task_of_other_assignee(tmp_path) -> None:
    service, repository, _, _, child_id, _ = _build_service(tmp_path)
    top = repository.get_user_by_name("山田")
    child = repository.get_task(child_id)
    assert top is not None
    assert child is not None

    service.split_task(
        parent_task_id=child_id,
        operator_user_id=top.Id,
        children=[
            {
                "Name": "top分割1",
                "AssignedManHour": 120,
                "EndDate": child.EndDate.isoformat(),
                "Memo": "",
                "AssigneeUserId": top.Id,
            },
            {
                "Name": "top分割2",
                "AssignedManHour": 180,
                "EndDate": child.EndDate.isoformat(),
                "Memo": "",
            },
        ],
    )

    created = [task for task in repository.list_tasks(child.ProjectId) if task.ParentTaskId == child_id]
    assert len(created) == 2


def test_set_task_assigned_manhour_keeps_child_estimate_balances(tmp_path) -> None:
    service, repository, member_id, parent_id, child_id, _ = _build_service(tmp_path)
    child = repository.get_task(child_id)
    assert child is not None

    repository.create_task(
        NewTaskInput(
            ProjectId=child.ProjectId,
            ParentTaskId=child_id,
            Name="孫1",
            Type="Task",
            AssigneeUserId=member_id,
            CreatorUserId=member_id,
            StartDate=date.today(),
            EndDate=child.EndDate,
            AssignedManHour=120,
            PlannedDeliverables=[],
        )
    )
    repository.create_task(
        NewTaskInput(
            ProjectId=child.ProjectId,
            ParentTaskId=child_id,
            Name="孫2",
            Type="Task",
            AssigneeUserId=member_id,
            CreatorUserId=member_id,
            StartDate=date.today(),
            EndDate=child.EndDate,
            AssignedManHour=180,
            PlannedDeliverables=[],
        )
    )

    service.set_root_task_assigned_manhour(
        task_id=parent_id,
        assigned_manhour=1000,
        operator_user_id=member_id,
    )

    tasks = repository.list_tasks(child.ProjectId)
    by_id = {task.Id: task for task in tasks}
    parent = by_id[parent_id]
    child = by_id[child_id]
    sibling = next(task for task in tasks if task.ParentTaskId == parent_id and task.Id != child_id)
    grand_children = [task for task in tasks if task.ParentTaskId == child_id]

    assert parent.AssignedManHour == 1000
    assert child.AssignedManHour == 300
    assert sibling.AssignedManHour == 300
    assert sorted(task.AssignedManHour for task in grand_children) == [120, 180]


def test_set_task_assigned_manhour_allows_non_root(tmp_path) -> None:
    service, repository, member_id, _, child_id, _ = _build_service(tmp_path)

    service.set_root_task_assigned_manhour(
        task_id=child_id,
        assigned_manhour=0,
        operator_user_id=member_id,
    )

    child = repository.get_task(child_id)
    assert child is not None
    assert child.AssignedManHour == 0


def test_list_tasks_reports_estimate_balance_and_signal_levels(tmp_path) -> None:
    service, repository, member_id, parent_id, child_id, _ = _build_service(tmp_path)
    top = repository.get_top_manager()
    child = repository.get_task(child_id)
    assert top is not None and child is not None

    root_without_estimate = repository.create_task(
        NewTaskInput(
            ProjectId=child.ProjectId,
            ParentTaskId=None,
            Name="root-no-estimate",
            Type="GroupTask",
            AssigneeUserId=member_id,
            CreatorUserId=top.Id,
            StartDate=date.today(),
            EndDate=child.EndDate,
            AssignedManHour=0,
            PlannedDeliverables=[],
        )
    )
    delegated_without_estimate = repository.create_task(
        NewTaskInput(
            ProjectId=child.ProjectId,
            ParentTaskId=parent_id,
            Name="delegated-no-estimate",
            Type="Task",
            AssigneeUserId=top.Id,
            CreatorUserId=top.Id,
            StartDate=date.today(),
            EndDate=child.EndDate,
            AssignedManHour=0,
            PlannedDeliverables=[],
        )
    )
    self_split_without_estimate = repository.create_task(
        NewTaskInput(
            ProjectId=child.ProjectId,
            ParentTaskId=child_id,
            Name="self-split-no-estimate",
            Type="Task",
            AssigneeUserId=member_id,
            CreatorUserId=top.Id,
            StartDate=date.today(),
            EndDate=child.EndDate,
            AssignedManHour=0,
            PlannedDeliverables=[],
        )
    )

    by_id = {task.Id: task for task in service.list_tasks()}
    assert by_id[parent_id].ChildAssignedManHour == 600
    assert by_id[parent_id].EstimateBalanceManHour == 0
    assert by_id[child_id].EstimateBalanceManHour == 300
    assert by_id[root_without_estimate.Id].EstimateSignal == "Strong"
    assert by_id[delegated_without_estimate.Id].EstimateSignal == "Medium"
    assert by_id[self_split_without_estimate.Id].EstimateSignal == "Weak"


def test_split_task_allows_zero_actual_child_and_preserves_parent_actual_total(tmp_path) -> None:
    service, repository, member_id, _, child_id, _ = _build_service(tmp_path)
    repository.update_task_actual_manhour(child_id, 90)
    child = repository.get_task(child_id)
    assert child is not None

    service.split_task(
        parent_task_id=child_id,
        operator_user_id=member_id,
        children=[
            {
                "Name": "A",
                "AssignedManHour": 150,
                "ActualManHour": 0,
                "EndDate": child.EndDate.isoformat(),
                "Memo": "",
            },
            {
                "Name": "B",
                "AssignedManHour": 150,
                "ActualManHour": 90,
                "EndDate": child.EndDate.isoformat(),
                "Memo": "",
            },
        ],
    )

    created = [task for task in repository.list_tasks(child.ProjectId) if task.ParentTaskId == child_id]
    assert sorted(task.ActualManHour for task in created) == [0, 90]


def test_split_task_sets_children_actual_and_clears_parent_actual(tmp_path) -> None:
    service, repository, member_id, _, child_id, _ = _build_service(tmp_path)
    repository.update_task_actual_manhour(child_id, 90)
    child = repository.get_task(child_id)
    assert child is not None

    service.split_task(
        parent_task_id=child_id,
        operator_user_id=member_id,
        children=[
            {
                "Name": "A",
                "AssignedManHour": 100,
                "ActualManHour": 30,
                "EndDate": child.EndDate.isoformat(),
                "Memo": "",
            },
            {
                "Name": "B",
                "AssignedManHour": 200,
                "ActualManHour": 60,
                "EndDate": child.EndDate.isoformat(),
                "Memo": "",
            },
        ],
    )

    parent_after = repository.get_task(child_id)
    assert parent_after is not None
    assert parent_after.ActualManHour == 0

    created = [task for task in repository.list_tasks(child.ProjectId) if task.ParentTaskId == child_id]
    assert sorted(task.ActualManHour for task in created) == [30, 60]


def test_split_task_rejects_actual_total_mismatch(tmp_path) -> None:
    service, repository, member_id, _, child_id, _ = _build_service(tmp_path)
    repository.update_task_actual_manhour(child_id, 90)
    child = repository.get_task(child_id)
    assert child is not None

    with pytest.raises(UserInputError):
        service.split_task(
            parent_task_id=child_id,
            operator_user_id=member_id,
            children=[
                {
                    "Name": "A",
                    "AssignedManHour": 100,
                    "ActualManHour": 0,
                    "EndDate": child.EndDate.isoformat(),
                    "Memo": "",
                },
                {
                    "Name": "B",
                    "AssignedManHour": 200,
                    "ActualManHour": 0,
                    "EndDate": child.EndDate.isoformat(),
                    "Memo": "",
                },
            ],
        )


def test_resplit_non_leaf_updates_existing_and_adds_new_child(tmp_path) -> None:
    service, repository, member_id, parent_id, child_id, child2_id = _build_service(tmp_path)

    repository.update_task_actual_manhour(child_id, 120)
    repository.update_task_actual_manhour(child2_id, 60)

    child = repository.get_task(child_id)
    child2 = repository.get_task(child2_id)
    assert child is not None
    assert child2 is not None

    service.split_task(
        parent_task_id=parent_id,
        operator_user_id=member_id,
        children=[
            {
                "ExistingTaskId": child_id,
                "Name": "既存子1(更新)",
                "AssignedManHour": 240,
                "ActualManHour": 999,
                "EndDate": (date.today() + timedelta(days=10)).isoformat(),
                "Memo": "memo-1",
                "AssigneeUserId": child.AssigneeUserId,
            },
            {
                "ExistingTaskId": child2_id,
                "Name": "既存子2(更新)",
                "AssignedManHour": 240,
                "ActualManHour": 999,
                "EndDate": (date.today() + timedelta(days=12)).isoformat(),
                "Memo": "memo-2",
                "AssigneeUserId": child2.AssigneeUserId,
            },
            {
                "Name": "新規子",
                "AssignedManHour": 120,
                "ActualManHour": 0,
                "EndDate": (date.today() + timedelta(days=14)).isoformat(),
                "Memo": "memo-new",
                "AssigneeUserId": member_id,
            },
        ],
    )

    parent_after = repository.get_task(parent_id)
    updated_child = repository.get_task(child_id)
    updated_child2 = repository.get_task(child2_id)
    assert parent_after is not None
    assert updated_child is not None
    assert updated_child2 is not None

    assert parent_after.Type == "GroupTask"
    assert updated_child.Name == "既存子1(更新)"
    assert updated_child.Memo == "memo-1"
    assert updated_child.AssigneeUserId == child.AssigneeUserId
    assert updated_child.ActualManHour == 120
    assert updated_child.AssignedManHour == 240

    assert updated_child2.Name == "既存子2(更新)"
    assert updated_child2.Memo == "memo-2"
    assert updated_child2.AssigneeUserId == child2.AssigneeUserId
    assert updated_child2.ActualManHour == 60
    assert updated_child2.AssignedManHour == 240

    all_children = [task for task in repository.list_tasks(parent_after.ProjectId) if task.ParentTaskId == parent_id]
    new_children = [task for task in all_children if task.Id not in {child_id, child2_id}]
    assert len(new_children) == 1
    assert new_children[0].Name == "新規子"
    assert new_children[0].AssignedManHour == 120
    assert new_children[0].ActualManHour == 0
    assert new_children[0].AssigneeUserId == member_id


def test_resplit_requires_all_existing_children(tmp_path) -> None:
    service, _, member_id, parent_id, child_id, _ = _build_service(tmp_path)

    with pytest.raises(UserInputError):
        service.split_task(
            parent_task_id=parent_id,
            operator_user_id=member_id,
            children=[
                {
                    "ExistingTaskId": child_id,
                    "Name": "既存子のみ",
                    "AssignedManHour": 300,
                    "ActualManHour": 0,
                    "EndDate": (date.today() + timedelta(days=10)).isoformat(),
                    "Memo": "",
                },
                {
                    "Name": "新規子",
                    "AssignedManHour": 300,
                    "ActualManHour": 0,
                    "EndDate": (date.today() + timedelta(days=12)).isoformat(),
                    "Memo": "",
                    "AssigneeUserId": member_id,
                },
            ],
        )


def test_resplit_rejects_existing_assignee_change(tmp_path) -> None:
    service, repository, member_id, parent_id, child_id, child2_id = _build_service(tmp_path)
    top = repository.get_top_manager()
    assert top is not None

    with pytest.raises(UserInputError):
        service.split_task(
            parent_task_id=parent_id,
            operator_user_id=member_id,
            children=[
                {
                    "ExistingTaskId": child_id,
                    "Name": "既存子1",
                    "AssignedManHour": 300,
                    "ActualManHour": 0,
                    "EndDate": (date.today() + timedelta(days=10)).isoformat(),
                    "Memo": "",
                    "AssigneeUserId": top.Id,
                },
                {
                    "ExistingTaskId": child2_id,
                    "Name": "既存子2",
                    "AssignedManHour": 300,
                    "ActualManHour": 0,
                    "EndDate": (date.today() + timedelta(days=12)).isoformat(),
                    "Memo": "",
                },
            ],
        )


def test_resplit_keeps_descendant_estimates_as_child_balance_changes(tmp_path) -> None:
    service, repository, member_id, parent_id, child_id, child2_id = _build_service(tmp_path)
    child = repository.get_task(child_id)
    assert child is not None

    repository.create_task(
        NewTaskInput(
            ProjectId=child.ProjectId,
            ParentTaskId=child_id,
            Name="孫1",
            Type="Task",
            AssigneeUserId=member_id,
            CreatorUserId=member_id,
            StartDate=date.today(),
            EndDate=child.EndDate,
            AssignedManHour=120,
            PlannedDeliverables=[],
        )
    )
    repository.create_task(
        NewTaskInput(
            ProjectId=child.ProjectId,
            ParentTaskId=child_id,
            Name="孫2",
            Type="Task",
            AssigneeUserId=member_id,
            CreatorUserId=member_id,
            StartDate=date.today(),
            EndDate=child.EndDate,
            AssignedManHour=180,
            PlannedDeliverables=[],
        )
    )

    service.split_task(
        parent_task_id=parent_id,
        operator_user_id=member_id,
        children=[
            {
                "ExistingTaskId": child_id,
                "Name": "既存子1",
                "AssignedManHour": 360,
                "ActualManHour": 0,
                "EndDate": child.EndDate.isoformat(),
                "Memo": "",
            },
            {
                "ExistingTaskId": child2_id,
                "Name": "既存子2",
                "AssignedManHour": 240,
                "ActualManHour": 0,
                "EndDate": child.EndDate.isoformat(),
                "Memo": "",
            },
        ],
    )

    tasks = repository.list_tasks(child.ProjectId)
    child_after = next(task for task in tasks if task.Id == child_id)
    child2_after = next(task for task in tasks if task.Id == child2_id)
    grand_children = [task for task in tasks if task.ParentTaskId == child_id]

    assert child_after.AssignedManHour == 360
    assert child2_after.AssignedManHour == 240
    assert sorted(task.AssignedManHour for task in grand_children) == [120, 180]


def test_resplit_allows_existing_child_past_due_date(tmp_path) -> None:
    service, repository, member_id, parent_id, child_id, child2_id = _build_service(tmp_path)

    past_due = date.today() - timedelta(days=1)
    child = repository.get_task(child_id)
    assert child is not None
    repository.update_task_assignee_and_end_date(child_id, child.AssigneeUserId, past_due)

    service.split_task(
        parent_task_id=parent_id,
        operator_user_id=member_id,
        children=[
            {
                "ExistingTaskId": child_id,
                "Name": "既存子1",
                "AssignedManHour": 300,
                "ActualManHour": 0,
                "EndDate": past_due.isoformat(),
                "Memo": "",
            },
            {
                "ExistingTaskId": child2_id,
                "Name": "既存子2",
                "AssignedManHour": 300,
                "ActualManHour": 0,
                "EndDate": (date.today() + timedelta(days=12)).isoformat(),
                "Memo": "",
            },
        ],
    )

    child_after = repository.get_task(child_id)
    assert child_after is not None
    assert child_after.EndDate == past_due


def test_task_child_dependency_mode_defaults_to_serial(tmp_path) -> None:
    _, repository, _, parent_id, child_id, _ = _build_service(tmp_path)

    parent = repository.get_task(parent_id)
    child = repository.get_task(child_id)
    assert parent is not None
    assert child is not None

    assert parent.ChildDependencyMode == "Serial"
    assert child.ChildDependencyMode == "Serial"


def test_update_task_child_dependency_mode_accepts_parallel(tmp_path) -> None:
    service, repository, member_id, parent_id, _, _ = _build_service(tmp_path)

    service.update_task_child_dependency_mode(
        task_id=parent_id,
        child_dependency_mode="Parallel",
        operator_user_id=member_id,
    )

    updated = repository.get_task(parent_id)
    assert updated is not None
    assert updated.ChildDependencyMode == "Parallel"


def test_update_task_child_dependency_mode_rejects_unknown_value(tmp_path) -> None:
    service, _, member_id, parent_id, _, _ = _build_service(tmp_path)

    with pytest.raises(UserInputError):
        service.update_task_child_dependency_mode(
            task_id=parent_id,
            child_dependency_mode="UnknownMode",
            operator_user_id=member_id,
        )


def test_update_task_child_dependency_mode_blocks_non_editor(tmp_path) -> None:
    service, repository, _, parent_id, _, _ = _build_service(tmp_path)
    top = repository.get_top_manager()
    assert top is not None

    existing_names = [user.Name for user in repository.list_users()]
    repository.ensure_users(
        names=[*existing_names, "visitor_for_dependency"],
        top_manager_name=top.Name,
        free_time_rate_default=0.2,
    )
    visitor = repository.get_user_by_name("visitor_for_dependency")
    assert visitor is not None

    with pytest.raises(UserInputError):
        service.update_task_child_dependency_mode(
            task_id=parent_id,
            child_dependency_mode="Parallel",
            operator_user_id=visitor.Id,
        )


def test_split_task_updates_parent_child_dependency_mode(tmp_path) -> None:
    service, repository, member_id, _, child_id, _ = _build_service(tmp_path)
    child = repository.get_task(child_id)
    assert child is not None

    service.split_task(
        parent_task_id=child_id,
        operator_user_id=member_id,
        children=[
            {
                "Name": "child-a",
                "AssignedManHour": 150,
                "EndDate": child.EndDate.isoformat(),
                "Memo": "",
            },
            {
                "Name": "child-b",
                "AssignedManHour": 150,
                "EndDate": child.EndDate.isoformat(),
                "Memo": "",
            },
        ],
        parent_child_dependency_mode="Parallel",
    )

    updated_parent = repository.get_task(child_id)
    assert updated_parent is not None
    assert updated_parent.ChildDependencyMode == "Parallel"


def test_bulk_update_task_fields_updates_all_fields_and_expands_parent(tmp_path) -> None:
    service, repository, member_id, parent_id, child_id, _ = _build_service(tmp_path)
    top = repository.get_top_manager()
    parent = repository.get_task(parent_id)
    assert top is not None and parent is not None
    next_due = parent.EndDate + timedelta(days=2)

    result = service.bulk_update_task_fields(
        operator_user_id=member_id,
        parent_task_id=parent_id,
        updates=[
            {
                "task_id": child_id,
                "name": "  更新した子タスク  ",
                "assigned_manhour": 420,
                "memo": "  更新メモ  ",
                "end_date": next_due.isoformat(),
                "assignee_user_id": top.Id,
            }
        ],
    )

    updated = repository.get_task(child_id)
    updated_parent = repository.get_task(parent_id)
    assert updated is not None and updated_parent is not None
    assert result["changed"] == 1
    assert result["failed"] == 0
    assert result["results"][0]["success"] is True
    assert any("自動調整" in warning for warning in result["results"][0]["warnings"])
    assert updated.Name == "更新した子タスク"
    assert updated.AssignedManHour == 420
    assert updated.Memo == "更新メモ"
    assert updated.EndDate == next_due
    assert updated.AssigneeUserId == top.Id
    assert updated_parent.EndDate == next_due


def test_bulk_update_task_fields_validates_whole_row_and_keeps_other_successes(tmp_path) -> None:
    service, repository, member_id, parent_id, child_id, child2_id = _build_service(tmp_path)
    top = repository.get_top_manager()
    failed_before = repository.get_task(child_id)
    valid_before = repository.get_task(child2_id)
    assert top is not None and failed_before is not None and valid_before is not None
    repository.update_task_actual_manhour(child_id, 30)

    result = service.bulk_update_task_fields(
        operator_user_id=member_id,
        parent_task_id=parent_id,
        updates=[
            {
                "task_id": child_id,
                "name": "保存されない名前",
                "assigned_manhour": 999,
                "memo": "保存されないメモ",
                "end_date": (failed_before.EndDate + timedelta(days=3)).isoformat(),
                "assignee_user_id": top.Id,
            },
            {
                "task_id": child2_id,
                "name": valid_before.Name,
                "assigned_manhour": 180,
                "memo": "成功行",
                "end_date": valid_before.EndDate.isoformat(),
                "assignee_user_id": valid_before.AssigneeUserId,
            },
        ],
    )

    failed_after = repository.get_task(child_id)
    valid_after = repository.get_task(child2_id)
    assert failed_after is not None and valid_after is not None
    assert result["changed"] == 1
    assert result["failed"] == 1
    assert [row["success"] for row in result["results"]] == [False, True]
    assert "実績MH" in result["results"][0]["message"]
    assert failed_after.Name == failed_before.Name
    assert failed_after.AssignedManHour == failed_before.AssignedManHour
    assert failed_after.Memo == failed_before.Memo
    assert failed_after.EndDate == failed_before.EndDate
    assert failed_after.AssigneeUserId == failed_before.AssigneeUserId
    assert valid_after.AssignedManHour == 180
    assert valid_after.Memo == "成功行"


def test_bulk_update_task_fields_allows_direct_parent_assignee_and_rejects_completion(tmp_path) -> None:
    service, repository, member_id, parent_id, child_id, child2_id = _build_service(tmp_path)
    top = repository.get_top_manager()
    child = repository.get_task(child_id)
    child2 = repository.get_task(child2_id)
    assert top is not None and child is not None and child2 is not None
    repository.update_task_assignee_and_end_date(child_id, top.Id, child.EndDate)
    repository.update_task_status(child2_id, "Completion", 100.0)

    result = service.bulk_update_task_fields(
        operator_user_id=member_id,
        parent_task_id=parent_id,
        updates=[
            {
                "task_id": child_id,
                "name": "直上担当者による更新",
                "assigned_manhour": child.AssignedManHour,
                "memo": child.Memo,
                "end_date": child.EndDate.isoformat(),
                "assignee_user_id": top.Id,
            },
            {
                "task_id": child2_id,
                "name": "完了後の更新",
                "assigned_manhour": child2.AssignedManHour,
                "memo": child2.Memo,
                "end_date": child2.EndDate.isoformat(),
                "assignee_user_id": child2.AssigneeUserId,
            },
        ],
    )

    updated_child = repository.get_task(child_id)
    completed_child = repository.get_task(child2_id)
    assert updated_child is not None and completed_child is not None
    assert updated_child.Name == "直上担当者による更新"
    assert completed_child.Name == child2.Name
    assert result["changed"] == 1
    assert result["failed"] == 1
    assert "完了タスク" in result["results"][1]["message"]


def test_bulk_update_task_fields_rejects_task_outside_selected_parent(tmp_path) -> None:
    service, repository, member_id, _, child_id, _ = _build_service(tmp_path)
    child = repository.get_task(child_id)
    assert child is not None

    result = service.bulk_update_task_fields(
        operator_user_id=member_id,
        parent_task_id=child_id,
        updates=[
            {
                "task_id": child_id,
                "name": "保存されない名前",
                "assigned_manhour": child.AssignedManHour,
                "memo": child.Memo,
                "end_date": child.EndDate.isoformat(),
                "assignee_user_id": child.AssigneeUserId,
            }
        ],
    )

    assert result["changed"] == 0
    assert result["failed"] == 1
    assert "直接の子タスク" in result["results"][0]["message"]
    assert repository.get_task(child_id).Name == child.Name
