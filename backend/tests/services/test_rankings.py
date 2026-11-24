from __future__ import annotations

from datetime import date, datetime, timedelta

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
from app.tms_core.services.core import TmsService


def _build_service(tmp_path) -> tuple[TmsService, TmsRepository, dict[str, str]]:
    db_path = tmp_path / "rankings.db"
    engine = create_sqlite_engine(db_path)
    create_all_tables(engine)
    repository = TmsRepository(create_session_factory(engine))

    repository.ensure_users(
        names=["top", "worker", "mentor", "other"],
        top_manager_name="top",
        free_time_rate_default=0.2,
    )
    project = repository.ensure_single_project(
        name="QOLONY",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        crash_zone_pct=20.0,
    )

    top = repository.get_user_by_name("top")
    worker = repository.get_user_by_name("worker")
    mentor = repository.get_user_by_name("mentor")
    other = repository.get_user_by_name("other")
    assert top is not None and worker is not None and mentor is not None and other is not None

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
            project_start_date=date(2026, 1, 1),
            project_end_date=date(2026, 12, 31),
            default_education_rate_monthly=0.1,
        ),
        users=UsersConfig(top_manager="top", members=["top", "worker", "mentor", "other"]),
        calendar=CalendarConfig(holidays=set()),
        initial_tasks=[],
        split_templates=[],
    )
    service = TmsService(settings=settings, repository=repository)
    return service, repository, {
        "project": project.Id,
        "top": top.Id,
        "worker": worker.Id,
        "mentor": mentor.Id,
        "other": other.Id,
    }


def _create_task(
    repository: TmsRepository,
    *,
    project_id: str,
    parent_task_id: str | None,
    name: str,
    assignee_user_id: str,
    creator_user_id: str,
    start_date: date,
    end_date: date,
    assigned_manhour: int,
    actual_manhour: int,
    completed_at: datetime | None,
    task_type: str = "Task",
) -> str:
    task = repository.create_task(
        NewTaskInput(
            ProjectId=project_id,
            ParentTaskId=parent_task_id,
            Name=name,
            Type=task_type,
            AssigneeUserId=assignee_user_id,
            CreatorUserId=creator_user_id,
            StartDate=start_date,
            EndDate=end_date,
            AssignedManHour=assigned_manhour,
            PlannedDeliverables=[],
        )
    )
    repository.update_task_actual_manhour(task.Id, actual_manhour)
    with repository._session() as session:
        entity = session.get(Task, task.Id)
        assert entity is not None
        entity.Status = "Completion" if completed_at is not None else "DuringStart"
        entity.ProgressRate = 100.0 if completed_at is not None else 50.0
        entity.CompletedAt = completed_at
    return task.Id


def test_rankings_use_completed_estimate_balances_and_support_multi_month_windows(tmp_path) -> None:
    service, repository, ids = _build_service(tmp_path)

    parent_id = _create_task(
        repository,
        project_id=ids["project"],
        parent_task_id=None,
        name="parent-group",
        assignee_user_id=ids["worker"],
        creator_user_id=ids["top"],
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 30),
        assigned_manhour=600,
        actual_manhour=600,
        completed_at=datetime(2026, 4, 30, 18, 0, 0),
        task_type="GroupTask",
    )

    april_fast_id = _create_task(
        repository,
        project_id=ids["project"],
        parent_task_id=parent_id,
        name="april-fast",
        assignee_user_id=ids["worker"],
        creator_user_id=ids["top"],
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 10),
        assigned_manhour=120,
        actual_manhour=120,
        completed_at=datetime(2026, 4, 10, 18, 0, 0),
    )
    april_ojt_id = _create_task(
        repository,
        project_id=ids["project"],
        parent_task_id=parent_id,
        name="april-ojt",
        assignee_user_id=ids["worker"],
        creator_user_id=ids["top"],
        start_date=date(2026, 4, 5),
        end_date=date(2026, 4, 18),
        assigned_manhour=60,
        actual_manhour=150,
        completed_at=datetime(2026, 4, 18, 18, 0, 0),
    )
    _create_task(
        repository,
        project_id=ids["project"],
        parent_task_id=None,
        name="march-task",
        assignee_user_id=ids["worker"],
        creator_user_id=ids["top"],
        start_date=date(2026, 3, 1),
        end_date=date(2026, 3, 5),
        assigned_manhour=30,
        actual_manhour=60,
        completed_at=datetime(2026, 3, 5, 18, 0, 0),
    )
    _create_task(
        repository,
        project_id=ids["project"],
        parent_task_id=None,
        name="other-april",
        assignee_user_id=ids["other"],
        creator_user_id=ids["top"],
        start_date=date(2026, 4, 2),
        end_date=date(2026, 4, 6),
        assigned_manhour=180,
        actual_manhour=180,
        completed_at=datetime(2026, 4, 6, 18, 0, 0),
    )

    repository.add_raw_worklog(
        user_id=ids["worker"],
        task_id=april_fast_id,
        start_at=datetime(2026, 4, 10, 9, 0, 0),
        end_at=datetime(2026, 4, 10, 11, 0, 0),
        duration_seconds=120 * 60,
        is_ojt=False,
        teacher_user_id=None,
        source_timer_id=None,
    )
    repository.add_raw_worklog(
        user_id=ids["worker"],
        task_id=april_ojt_id,
        start_at=datetime(2026, 4, 18, 9, 0, 0),
        end_at=datetime(2026, 4, 18, 10, 30, 0),
        duration_seconds=90 * 60,
        is_ojt=True,
        teacher_user_id=ids["mentor"],
        source_timer_id=None,
    )
    repository.add_raw_worklog(
        user_id=ids["worker"],
        task_id=april_ojt_id,
        start_at=datetime(2026, 4, 18, 10, 30, 0),
        end_at=datetime(2026, 4, 18, 11, 30, 0),
        duration_seconds=60 * 60,
        is_ojt=False,
        teacher_user_id=None,
        source_timer_id=None,
    )

    april_rows = {row.UserId: row for row in service._build_ranking_metrics("2026-04", 1)}
    quarter_rows = {row.UserId: row for row in service._build_ranking_metrics("2026-04", 3)}

    worker_april = april_rows[ids["worker"]]
    mentor_april = april_rows[ids["mentor"]]
    worker_quarter = quarter_rows[ids["worker"]]

    assert worker_april.OutputVolume == 600
    assert round(worker_april.WorkSpeed, 2) == 222.22
    assert mentor_april.EducationEfficiency == 80.0
    assert mentor_april.TeachingTime == 90

    assert worker_quarter.OutputVolume == 630
    assert round(worker_quarter.WorkSpeed, 2) == 190.91

    rankings = service.get_rankings(self_user_id=ids["worker"], year_month="2026-04", period_months=1)
    assert set(rankings.keys()) == {
        "OutputVolume",
        "WorkSpeed",
        "EducationEfficiency",
        "TeachingTime",
    }
    assert len(rankings["OutputVolume"]) == 2

    admin_rankings = service.get_rankings(self_user_id=ids["top"], year_month="2026-04", period_months=1)
    assert len(admin_rankings["OutputVolume"]) == 4
    assert [item.IsPublished for item in admin_rankings["OutputVolume"]] == [True, True, False, False]

    histories = service.get_ranking_histories(
        self_user_id=ids["worker"],
        year_month="2026-04",
        period_months=1,
    )
    assert len(histories["OutputVolume"]) == 1
    worker_history = histories["OutputVolume"][0]
    assert worker_history.UserId == ids["worker"]
    assert [point.Date.isoformat() for point in worker_history.Points] == [
        "2026-01-01",
        "2026-02-01",
        "2026-03-01",
        "2026-04-01",
    ]
    assert [point.Value for point in worker_history.Points] == [0.0, 0.0, 30.0, 600.0]

    quarter_history = service.get_ranking_histories(
        self_user_id=ids["worker"],
        year_month="2026-04",
        period_months=3,
    )
    assert [point.Date.isoformat() for point in quarter_history["OutputVolume"][0].Points] == [
        "2026-01-01",
        "2026-02-01",
        "2026-03-01",
        "2026-04-01",
    ]
    assert [point.Value for point in quarter_history["OutputVolume"][0].Points] == [0.0, 0.0, 30.0, 630.0]

    admin_history = service.get_ranking_histories(
        self_user_id=ids["top"],
        year_month="2026-04",
        period_months=1,
    )
    assert len(admin_history["OutputVolume"]) == 4
