from datetime import date, datetime, timedelta

from app.tms_core.config import (
    AppConfig,
    CalendarConfig,
    IQuavisConfig,
    Settings,
    TeamConfig,
    UsersConfig,
)
from app.tms_core.db.models import DailyProgressSnapshot, ProgressRateHistory, Task
from app.tms_core.db.repository import NewTaskInput, TmsRepository
from app.tms_core.db.session import create_all_tables, create_session_factory, create_sqlite_engine
from app.tms_core.services.core import TmsService


def _build_service(tmp_path, *, holidays: set[date], free_time_rate_default: float = 0.0) -> tuple[TmsService, TmsRepository, str, str]:
    db_path = tmp_path / "progress_dashboard.db"
    engine = create_sqlite_engine(db_path)
    create_all_tables(engine)
    repository = TmsRepository(create_session_factory(engine))

    repository.ensure_users(
        names=["top", "member"],
        top_manager_name="top",
        free_time_rate_default=free_time_rate_default,
    )
    project = repository.ensure_single_project(
        name="QOLONY",
        start_date=date(2026, 2, 1),
        end_date=date(2026, 12, 31),
        crash_zone_pct=20.0,
    )

    top = repository.get_user_by_name("top")
    member = repository.get_user_by_name("member")
    assert top is not None
    assert member is not None

    task = repository.create_task(
        NewTaskInput(
            ProjectId=project.Id,
            ParentTaskId=None,
            Name="task-a",
            Type="Task",
            AssigneeUserId=member.Id,
            CreatorUserId=top.Id,
            StartDate=date(2026, 2, 10),
            EndDate=date(2026, 2, 14),
            AssignedManHour=300,
            PlannedDeliverables=[],
        )
    )
    with repository._session() as session:
        entity = session.get(Task, task.Id)
        assert entity is not None
        entity.CreateDate = datetime(2026, 2, 9, 9, 0, 0)

    settings = Settings(
        app=AppConfig(
            db_path=db_path,
            crash_zone_pct=20.0,
            task_signal_capacity_minutes_per_day=240,
            seed_initial_tasks_on_empty_db=False,
            seed_demo_data_on_empty_db=False,
            free_time_rate_default=free_time_rate_default,
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
            project_start_date=date(2026, 2, 1),
            project_end_date=date(2026, 12, 31),
            default_education_rate_monthly=0.0,
        ),
        users=UsersConfig(top_manager="top", members=["top", "member"]),
        calendar=CalendarConfig(holidays=holidays),
        initial_tasks=[],
        split_templates=[],
    )
    service = TmsService(settings=settings, repository=repository)
    return service, repository, member.Id, task.Id


def test_task_progress_dashboard_excludes_holidays_and_builds_daily_speed(tmp_path) -> None:
    service, repository, member_id, task_id = _build_service(
        tmp_path,
        holidays={date(2026, 2, 11)},
    )

    repository.replace_daily_task_actuals(
        project_id=service.get_project().Id,
        user_id=member_id,
        work_date=date(2026, 2, 10),
        rows=[
            {
                "task_id": task_id,
                "timer_minutes": 60.0,
                "distributed_minutes": 0.0,
            }
        ],
    )
    repository.replace_daily_task_actuals(
        project_id=service.get_project().Id,
        user_id=member_id,
        work_date=date(2026, 2, 12),
        rows=[
            {
                "task_id": task_id,
                "timer_minutes": 60.0,
                "distributed_minutes": 0.0,
            }
        ],
    )

    dashboard = service.get_task_progress_dashboard(task_id=task_id, today=date(2026, 2, 13))

    assert [point.Date for point in dashboard.ProgressPoints] == [
        date(2026, 2, 10),
        date(2026, 2, 12),
        date(2026, 2, 13),
    ]
    assert round(dashboard.ProgressPoints[-1].ProgressRate, 2) == 40.0
    assert [round(point.VActual, 2) for point in dashboard.SpeedPoints] == [60.0, 60.0, 0.0]
    assert [round(point.VAverage, 2) for point in dashboard.SpeedPoints] == [60.0, 60.0, 60.0]
    assert round(dashboard.SpeedPoints[-1].VActual, 2) == 0.0


def test_recompute_daily_actuals_distributes_unknown_minutes_to_today_tasks(tmp_path) -> None:
    service, repository, member_id, task1_id = _build_service(
        tmp_path,
        holidays=set(),
        free_time_rate_default=0.0,
    )
    project = service.get_project()
    task1 = repository.get_task(task1_id)
    assert task1 is not None

    task2 = repository.create_task(
        NewTaskInput(
            ProjectId=project.Id,
            ParentTaskId=None,
            Name="task-b",
            Type="Task",
            AssigneeUserId=member_id,
            CreatorUserId=task1.CreatorUserId,
            StartDate=date(2026, 2, 16),
            EndDate=date(2026, 2, 17),
            AssignedManHour=300,
            PlannedDeliverables=[],
        )
    )

    work_date = date(2026, 2, 16)
    start_at = datetime(2026, 2, 16, 9, 0, 0)
    end_at = start_at + timedelta(hours=2)

    repository.add_raw_worklog(
        user_id=member_id,
        task_id=task1_id,
        start_at=start_at,
        end_at=end_at,
        duration_seconds=(end_at - start_at).total_seconds(),
        is_ojt=False,
        teacher_user_id=None,
        source_timer_id=None,
    )
    repository.upsert_daily_attendance(
        user_id=member_id,
        work_date=work_date,
        start_at=start_at,
        end_at=start_at + timedelta(hours=8),
        work_minutes=480,
    )

    touched = service._recompute_daily_task_actuals_for_user_day(user_id=member_id, work_date=work_date)
    assert task1_id in touched
    assert task2.Id in touched

    rows = repository.list_daily_task_actuals_by_user_and_day(user_id=member_id, work_date=work_date)
    total_by_task = {row.TaskId: row.TotalMinutes for row in rows}

    assert round(total_by_task[task1_id], 4) == 120.0
    assert round(total_by_task[task2.Id], 4) == 360.0


def test_parent_progress_uses_completed_children_only_for_progress_and_speed(tmp_path) -> None:
    service, repository, member_id, _ = _build_service(
        tmp_path,
        holidays={date(2026, 2, 11)},
        free_time_rate_default=0.0,
    )
    project = service.get_project()
    top = repository.get_user_by_name("top")
    assert top is not None

    parent = repository.create_task(
        NewTaskInput(
            ProjectId=project.Id,
            ParentTaskId=None,
            Name="parent",
            Type="GroupTask",
            AssigneeUserId=member_id,
            CreatorUserId=top.Id,
            StartDate=date(2026, 2, 9),
            EndDate=date(2026, 2, 14),
            AssignedManHour=300,
            PlannedDeliverables=[],
        )
    )
    child1 = repository.create_task(
        NewTaskInput(
            ProjectId=project.Id,
            ParentTaskId=parent.Id,
            Name="child-1",
            Type="Task",
            AssigneeUserId=member_id,
            CreatorUserId=top.Id,
            StartDate=date(2026, 2, 10),
            EndDate=date(2026, 2, 13),
            AssignedManHour=100,
            PlannedDeliverables=[],
        )
    )
    child2 = repository.create_task(
        NewTaskInput(
            ProjectId=project.Id,
            ParentTaskId=parent.Id,
            Name="child-2",
            Type="Task",
            AssigneeUserId=member_id,
            CreatorUserId=top.Id,
            StartDate=date(2026, 2, 10),
            EndDate=date(2026, 2, 13),
            AssignedManHour=200,
            PlannedDeliverables=[],
        )
    )

    with repository._session() as session:
        for task_id in (parent.Id, child1.Id, child2.Id):
            entity = session.get(Task, task_id)
            assert entity is not None
            entity.CreateDate = datetime(2026, 2, 9, 9, 0, 0)

        child1_entity = session.get(Task, child1.Id)
        assert child1_entity is not None
        child1_entity.Status = "Completion"
        child1_entity.CompletedAt = datetime(2026, 2, 12, 12, 0, 0)

    repository.replace_daily_task_actuals(
        project_id=project.Id,
        user_id=member_id,
        work_date=date(2026, 2, 10),
        rows=[
            {
                "task_id": child1.Id,
                "timer_minutes": 180.0,
                "distributed_minutes": 0.0,
            }
        ],
    )
    repository.replace_daily_task_actuals(
        project_id=project.Id,
        user_id=member_id,
        work_date=date(2026, 2, 13),
        rows=[
            {
                "task_id": child2.Id,
                "timer_minutes": 60.0,
                "distributed_minutes": 0.0,
            }
        ],
    )

    dashboard = service.get_task_progress_dashboard(task_id=parent.Id, today=date(2026, 2, 13))
    by_date = {point.Date: point for point in dashboard.ProgressPoints}
    speed_by_date = {point.Date: point for point in dashboard.SpeedPoints}

    assert round(dashboard.AssignedTotalMinutes, 2) == 300.0
    assert round(by_date[date(2026, 2, 10)].ProgressRate, 2) == 0.0
    assert round(by_date[date(2026, 2, 12)].ProgressRate, 2) == 33.33
    assert round(speed_by_date[date(2026, 2, 12)].VActual, 2) == 100.0

def test_finalize_daily_actuals_if_due_runs_once_per_day(tmp_path) -> None:
    service, repository, member_id, task_id = _build_service(
        tmp_path,
        holidays=set(),
        free_time_rate_default=0.0,
    )

    work_date = date(2026, 2, 16)
    start_at = datetime(2026, 2, 16, 9, 0, 0)
    end_at = start_at + timedelta(hours=2)

    repository.add_raw_worklog(
        user_id=member_id,
        task_id=task_id,
        start_at=start_at,
        end_at=end_at,
        duration_seconds=(end_at - start_at).total_seconds(),
        is_ojt=False,
        teacher_user_id=None,
        source_timer_id=None,
    )
    repository.upsert_daily_attendance(
        user_id=member_id,
        work_date=work_date,
        start_at=start_at,
        end_at=start_at + timedelta(hours=8),
        work_minutes=480,
    )

    executed_first = service.finalize_daily_actuals_if_due(now=datetime(2026, 2, 17, 9, 0, 0))
    rows_after_first = repository.list_daily_task_actuals_by_user_and_day(user_id=member_id, work_date=work_date)
    first_total = sum(row.TotalMinutes for row in rows_after_first)

    executed_second = service.finalize_daily_actuals_if_due(now=datetime(2026, 2, 17, 12, 0, 0))
    rows_after_second = repository.list_daily_task_actuals_by_user_and_day(user_id=member_id, work_date=work_date)
    second_total = sum(row.TotalMinutes for row in rows_after_second)

    assert executed_first is True
    assert executed_second is False
    assert repository.get_state("LastDailyActualFinalizeDate") == "2026-02-17"
    assert round(first_total, 4) == 480.0
    assert round(second_total, 4) == 480.0


def test_daily_snapshot_if_due_records_project_history_without_refreshing_all_tasks(tmp_path) -> None:
    service, repository, _, _ = _build_service(
        tmp_path,
        holidays=set(),
        free_time_rate_default=0.0,
    )

    executed_first = service.run_daily_snapshot_if_due(now=datetime(2026, 2, 17, 9, 0, 0))
    executed_second = service.run_daily_snapshot_if_due(now=datetime(2026, 2, 17, 12, 0, 0))

    with repository._session() as session:
        task_snapshot_count = session.query(DailyProgressSnapshot).count()
        project_history_count = session.query(ProgressRateHistory).count()

    assert executed_first is True
    assert executed_second is False
    assert repository.get_state("LastDailySnapshotDate") == "2026-02-17"
    assert project_history_count == 1
    assert task_snapshot_count == 0


