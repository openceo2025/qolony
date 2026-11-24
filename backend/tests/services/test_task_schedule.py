from datetime import date, timedelta

import pytest

from app.tms_core.config import AppConfig, CalendarConfig, IQuavisConfig, Settings, TeamConfig, UsersConfig
from app.tms_core.db.repository import NewTaskInput, TmsRepository
from app.tms_core.db.session import create_all_tables, create_session_factory, create_sqlite_engine
from app.tms_core.services.core import TmsService, UserInputError


def _build_service(tmp_path) -> tuple[TmsService, TmsRepository, str, str, str]:
    today = date.today()
    project_start = today - timedelta(days=30)
    project_end = today + timedelta(days=30)

    db_path = tmp_path / "task_schedule.db"
    engine = create_sqlite_engine(db_path)
    create_all_tables(engine)
    repository = TmsRepository(create_session_factory(engine))

    repository.ensure_users(
        names=["top", "member"],
        top_manager_name="top",
        free_time_rate_default=0.2,
    )
    project = repository.ensure_single_project(
        name="QOLONY",
        start_date=project_start,
        end_date=project_end,
        crash_zone_pct=20.0,
    )

    top = repository.get_user_by_name("top")
    member = repository.get_user_by_name("member")
    assert top is not None and member is not None

    parent = repository.create_task(
        NewTaskInput(
            ProjectId=project.Id,
            ParentTaskId=None,
            Name="parent",
            Type="GroupTask",
            AssigneeUserId=member.Id,
            CreatorUserId=top.Id,
            StartDate=today - timedelta(days=10),
            EndDate=today + timedelta(days=10),
            AssignedManHour=600,
            PlannedDeliverables=[],
        )
    )
    child = repository.create_task(
        NewTaskInput(
            ProjectId=project.Id,
            ParentTaskId=parent.Id,
            Name="child",
            Type="Task",
            AssigneeUserId=member.Id,
            CreatorUserId=top.Id,
            StartDate=today - timedelta(days=5),
            EndDate=today + timedelta(days=5),
            AssignedManHour=600,
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
        users=UsersConfig(top_manager="top", members=["top", "member"]),
        calendar=CalendarConfig(holidays=set()),
        initial_tasks=[],
        split_templates=[],
    )
    service = TmsService(settings=settings, repository=repository)
    service.recompute_all_task_progress()
    return service, repository, member.Id, parent.Id, child.Id


def _build_parent_only_service(tmp_path) -> tuple[TmsService, TmsRepository, str, str]:
    today = date.today()
    project_start = today - timedelta(days=30)
    project_end = today + timedelta(days=30)

    db_path = tmp_path / "task_schedule_parent_only.db"
    engine = create_sqlite_engine(db_path)
    create_all_tables(engine)
    repository = TmsRepository(create_session_factory(engine))

    repository.ensure_users(
        names=["top", "member"],
        top_manager_name="top",
        free_time_rate_default=0.2,
    )
    project = repository.ensure_single_project(
        name="QOLONY",
        start_date=project_start,
        end_date=project_end,
        crash_zone_pct=20.0,
    )

    top = repository.get_user_by_name("top")
    member = repository.get_user_by_name("member")
    assert top is not None and member is not None

    parent = repository.create_task(
        NewTaskInput(
            ProjectId=project.Id,
            ParentTaskId=None,
            Name="parent",
            Type="Task",
            AssigneeUserId=member.Id,
            CreatorUserId=top.Id,
            StartDate=today - timedelta(days=10),
            EndDate=today + timedelta(days=10),
            AssignedManHour=600,
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
        users=UsersConfig(top_manager="top", members=["top", "member"]),
        calendar=CalendarConfig(holidays=set()),
        initial_tasks=[],
        split_templates=[],
    )
    service = TmsService(settings=settings, repository=repository)
    service.recompute_all_task_progress()
    return service, repository, member.Id, parent.Id


def test_split_task_uses_create_date_for_new_child_start_date(tmp_path) -> None:
    service, repository, member_id, parent_id = _build_parent_only_service(tmp_path)
    parent = repository.get_task(parent_id)
    assert parent is not None

    service.split_task(
        parent_task_id=parent_id,
        operator_user_id=member_id,
        children=[
            {
                "Name": "split-child",
                "AssignedManHour": 600,
                "ActualManHour": 0,
                "EndDate": (date.today() + timedelta(days=3)).isoformat(),
                "Memo": "",
                "AssigneeUserId": member_id,
            }
        ],
    )

    tasks = repository.list_tasks(parent.ProjectId)
    split_child = next(task for task in tasks if task.ParentTaskId == parent_id and task.Name == "split-child")
    assert split_child.StartDate == split_child.CreateDate.date()
    assert split_child.StartDate != parent.StartDate


def test_update_task_schedule_expands_parent_when_child_moves_outside_parent_bounds(tmp_path, monkeypatch) -> None:
    service, repository, member_id, parent_id, child_id = _build_service(tmp_path)
    parent = repository.get_task(parent_id)
    child = repository.get_task(child_id)
    assert parent is not None and child is not None

    new_start = parent.StartDate - timedelta(days=1)
    new_end = parent.EndDate + timedelta(days=1)
    queued_task_ids: list[str] = []
    monkeypatch.setattr(service, "_queue_iquavis_schedule_sync", queued_task_ids.append)
    service.update_task_schedule(
        child_id,
        start_date=new_start,
        end_date=new_end,
        operator_user_id=member_id,
    )

    updated_parent = repository.get_task(parent_id)
    updated_child = repository.get_task(child_id)
    assert updated_parent is not None and updated_child is not None
    assert updated_child.StartDate == new_start
    assert updated_child.EndDate == new_end
    assert updated_parent.StartDate == new_start
    assert updated_parent.EndDate == new_end
    assert set(queued_task_ids) == {parent_id, child_id}


def test_update_task_schedule_allows_uncompleted_task_with_past_deadline(tmp_path) -> None:
    service, repository, member_id, parent_id, child_id = _build_service(tmp_path)
    parent = repository.get_task(parent_id)
    child = repository.get_task(child_id)
    assert parent is not None and child is not None

    new_start = parent.StartDate
    new_end = date.today() - timedelta(days=1)

    service.update_task_schedule(
        child_id,
        start_date=new_start,
        end_date=new_end,
        operator_user_id=member_id,
    )

    updated = repository.get_task(child_id)
    assert updated is not None
    assert updated.StartDate == new_start
    assert updated.EndDate == new_end


def test_update_task_schedule_allows_parent_inside_child_bounds_with_warning(tmp_path) -> None:
    service, repository, member_id, parent_id, child_id = _build_service(tmp_path)
    parent = repository.get_task(parent_id)
    child = repository.get_task(child_id)
    assert parent is not None and child is not None

    warnings = service.update_task_schedule(
        parent_id,
        start_date=child.StartDate + timedelta(days=1),
        end_date=child.EndDate - timedelta(days=1),
        operator_user_id=member_id,
    )

    updated = repository.get_task(parent_id)
    assert updated is not None
    assert updated.StartDate == child.StartDate + timedelta(days=1)
    assert updated.EndDate == child.EndDate - timedelta(days=1)
    assert any("配下タスク" in warning for warning in warnings)


def test_split_task_under_expired_parent_adjusts_dates_instead_of_rejecting(tmp_path) -> None:
    service, repository, member_id, parent_id = _build_parent_only_service(tmp_path)
    parent = repository.get_task(parent_id)
    assert parent is not None
    expired = date.today() - timedelta(days=2)
    repository.update_task_schedule(parent_id, parent.StartDate, expired)

    warnings = service.split_task(
        parent_task_id=parent_id,
        operator_user_id=member_id,
        children=[
            {
                "Name": "expired-parent-child",
                "AssignedManHour": 600,
                "ActualManHour": 0,
                "EndDate": expired.isoformat(),
                "Memo": "",
                "AssigneeUserId": member_id,
            }
        ],
    )

    created = next(
        task for task in repository.list_tasks(parent.ProjectId)
        if task.ParentTaskId == parent_id and task.Name == "expired-parent-child"
    )
    assert created.StartDate == expired
    assert created.EndDate == expired
    assert warnings == []


def test_update_task_schedule_allows_completed_task_with_past_deadline(tmp_path) -> None:
    service, repository, member_id, parent_id, child_id = _build_service(tmp_path)
    parent = repository.get_task(parent_id)
    child = repository.get_task(child_id)
    assert parent is not None and child is not None

    repository.update_task_status(child_id, status="Completion", progress_rate=100.0)

    new_start = child.StartDate - timedelta(days=2)
    new_end = date.today() - timedelta(days=1)

    service.update_task_schedule(
        child_id,
        start_date=new_start,
        end_date=new_end,
        operator_user_id=member_id,
    )

    updated = repository.get_task(child_id)
    assert updated is not None
    assert updated.StartDate == new_start
    assert updated.EndDate == new_end


def test_update_task_parent_keeps_iquavis_link_and_recomputes_local_tree(tmp_path) -> None:
    service, repository, member_id, parent_id, child_id = _build_service(tmp_path)
    top = repository.get_top_manager()
    project = repository.get_project()
    assert top is not None and project is not None
    today = date.today()
    new_parent = repository.create_task(
        NewTaskInput(
            ProjectId=project.Id,
            ParentTaskId=None,
            Name="new parent",
            Type="Task",
            AssigneeUserId=member_id,
            CreatorUserId=top.Id,
            StartDate=today - timedelta(days=6),
            EndDate=today + timedelta(days=6),
            AssignedManHour=120,
            PlannedDeliverables=[],
        )
    )
    repository.set_task_iquavis_link(child_id, iquavis_task_id="IQ-TASK", iquavis_project_id="P1")

    service.update_task_parent(
        task_id=child_id,
        parent_task_id=new_parent.Id,
        operator_user_id=member_id,
    )

    moved = repository.get_task(child_id)
    updated_parent = repository.get_task(new_parent.Id)
    old_parent = repository.get_task(parent_id)
    assert moved is not None and updated_parent is not None and old_parent is not None
    assert moved.ParentTaskId == new_parent.Id
    assert moved.IQuavisTaskId == "IQ-TASK"
    assert updated_parent.Type == "GroupTask"
    assert old_parent.IQuavisTaskId is None


def test_update_task_parent_rejects_cycle(tmp_path) -> None:
    service, _, member_id, parent_id, child_id = _build_service(tmp_path)

    with pytest.raises(UserInputError):
        service.update_task_parent(
            task_id=parent_id,
            parent_task_id=child_id,
            operator_user_id=member_id,
        )
