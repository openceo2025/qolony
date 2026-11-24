from datetime import date, datetime, timedelta

import pytest

import app.tms_core.services.core as core_module
from app.tms_core.config import (
    AppConfig,
    CalendarConfig,
    IQuavisConfig,
    Settings,
    TeamConfig,
    UsersConfig,
)
from app.tms_core.db.repository import NewTaskInput, TmsRepository
from app.tms_core.db.session import create_all_tables, create_session_factory, create_sqlite_engine
from app.tms_core.services.core import TmsService, UserInputError


def _build_service(tmp_path) -> tuple[TmsService, TmsRepository, str, str]:
    db_path = tmp_path / "members.db"
    engine = create_sqlite_engine(db_path)
    create_all_tables(engine)
    repository = TmsRepository(create_session_factory(engine))

    repository.ensure_users(
        names=["top", "member"],
        top_manager_name="top",
        free_time_rate_default=0.0,
    )
    project = repository.ensure_single_project(
        name="QOLONY",
        start_date=date(2026, 4, 1),
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
            Name="daily task",
            Type="Task",
            AssigneeUserId=member.Id,
            CreatorUserId=top.Id,
            StartDate=date(2026, 4, 10),
            EndDate=date(2026, 4, 10),
            AssignedManHour=480,
            PlannedDeliverables=[],
        )
    )
    repository.update_task_today_override(task.Id, True)

    settings = Settings(
        app=AppConfig(
            db_path=db_path,
            crash_zone_pct=20.0,
            task_signal_capacity_minutes_per_day=240,
            seed_initial_tasks_on_empty_db=False,
            seed_demo_data_on_empty_db=False,
            free_time_rate_default=0.0,
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
            project_start_date=date(2026, 4, 1),
            project_end_date=date(2026, 12, 31),
            default_education_rate_monthly=0.0,
        ),
        users=UsersConfig(top_manager="top", members=["top", "member"]),
        calendar=CalendarConfig(holidays=set()),
        initial_tasks=[],
        split_templates=[],
    )
    return TmsService(settings=settings, repository=repository), repository, member.Id, task.Id


def test_member_profile_is_editable_by_top_and_condition_only_by_self(tmp_path) -> None:
    service, repository, member_id, _ = _build_service(tmp_path)
    top = repository.get_user_by_name("top")
    assert top is not None

    service.update_member_profile(
        user_id=member_id,
        operator_user_id=top.Id,
        default_commit_rate=0.5,
        condition_icon="great",
        formation_role="設計",
        formation_position="",
        formation_x=38,
        formation_y=42,
        display_order=3,
        title_tags=["MVP", "MVP"],
        special_abilities=["レビュー"],
    )

    member = next(item for item in service.list_members(date_from=date(2026, 4, 1), date_to=date(2026, 4, 30)) if item.UserId == member_id)
    assert member.Profile.DefaultCommitRate == 0.5
    assert member.Profile.ConditionIcon == "great"
    assert member.Profile.FormationRole == "設計"
    assert member.Profile.FormationPosition == ""
    assert member.Profile.TitleTags == ["MVP"]
    assert member.Profile.SpecialAbilities == ["レビュー"]

    service.update_member_profile(
        user_id=member_id,
        operator_user_id=member_id,
        default_commit_rate=member.Profile.DefaultCommitRate,
        condition_icon="good",
        formation_role=member.Profile.FormationRole,
        formation_position=member.Profile.FormationPosition,
        formation_x=member.Profile.FormationX,
        formation_y=member.Profile.FormationY,
        display_order=member.Profile.DisplayOrder,
        title_tags=member.Profile.TitleTags,
        special_abilities=member.Profile.SpecialAbilities,
    )
    updated = next(item for item in service.list_members(date_from=date(2026, 4, 1), date_to=date(2026, 4, 30)) if item.UserId == member_id)
    assert updated.Profile.ConditionIcon == "good"

    with pytest.raises(UserInputError):
        service.update_member_profile(
            user_id=member_id,
            operator_user_id=member_id,
            default_commit_rate=0.25,
            condition_icon="good",
            formation_role=member.Profile.FormationRole,
            formation_position=member.Profile.FormationPosition,
            formation_x=member.Profile.FormationX,
            formation_y=member.Profile.FormationY,
            display_order=member.Profile.DisplayOrder,
            title_tags=member.Profile.TitleTags,
            special_abilities=member.Profile.SpecialAbilities,
        )


def test_daily_actual_finalize_uses_commit_rate_and_vacation(tmp_path) -> None:
    service, repository, member_id, task_id = _build_service(tmp_path)
    top = repository.get_user_by_name("top")
    assert top is not None
    work_date = date(2026, 4, 10)
    start_at = datetime(2026, 4, 10, 9, 0, 0)

    service.update_member_profile(
        user_id=member_id,
        operator_user_id=top.Id,
        default_commit_rate=0.5,
        condition_icon="normal",
        formation_role="",
        formation_position="",
        formation_x=50,
        formation_y=50,
        display_order=1,
        title_tags=[],
        special_abilities=[],
    )
    repository.upsert_daily_attendance(
        user_id=member_id,
        work_date=work_date,
        start_at=start_at,
        end_at=start_at + timedelta(hours=8),
        work_minutes=480,
    )

    touched = service._recompute_daily_task_actuals_for_user_day(user_id=member_id, work_date=work_date)
    rows = repository.list_daily_task_actuals_by_user_and_day(user_id=member_id, work_date=work_date)
    assert task_id in touched
    assert len(rows) == 1
    assert round(rows[0].TotalMinutes, 4) == 240.0

    service.upsert_member_day_config(
        user_id=member_id,
        operator_user_id=top.Id,
        work_date=work_date,
        is_vacation=True,
        leave_type="全休",
        commit_rate_override=None,
        note="休暇",
    )
    touched_after_vacation = service._recompute_daily_task_actuals_for_user_day(user_id=member_id, work_date=work_date)
    rows_after_vacation = repository.list_daily_task_actuals_by_user_and_day(
        user_id=member_id,
        work_date=work_date,
    )
    assert task_id in touched_after_vacation
    assert rows_after_vacation == []

    service.upsert_member_day_config(
        user_id=member_id,
        operator_user_id=top.Id,
        work_date=work_date,
        is_vacation=False,
        leave_type="AM半休",
        commit_rate_override=None,
        note="午前休",
    )
    service._recompute_daily_task_actuals_for_user_day(user_id=member_id, work_date=work_date)
    rows_after_half_day = repository.list_daily_task_actuals_by_user_and_day(
        user_id=member_id,
        work_date=work_date,
    )
    assert len(rows_after_half_day) == 1
    assert round(rows_after_half_day[0].TotalMinutes, 4) == 120.0


def test_timer_interval_records_ojt_teacher_only_when_timer_payload_has_teacher(tmp_path) -> None:
    service, repository, member_id, task_id = _build_service(tmp_path)
    top = repository.get_user_by_name("top")
    assert top is not None

    start_at = datetime(2026, 4, 10, 13, 0, 0)
    end_at = start_at + timedelta(minutes=45)
    service.record_timer_interval(
        user_id=member_id,
        task_id=task_id,
        started_at_ms=int(start_at.timestamp() * 1000),
        ended_at_ms=int(end_at.timestamp() * 1000),
        is_ojt=True,
        teacher_user_id=top.Id,
    )

    raw_worklogs = repository.list_raw_worklogs_by_user_and_day(
        user_id=member_id,
        work_date=start_at.date(),
    )
    assert len(raw_worklogs) == 1
    assert raw_worklogs[0].IsOjt is True
    assert raw_worklogs[0].TeacherUserId == top.Id

    with pytest.raises(UserInputError):
        service.record_timer_interval(
            user_id=member_id,
            task_id=task_id,
            started_at_ms=int((end_at + timedelta(minutes=1)).timestamp() * 1000),
            ended_at_ms=int((end_at + timedelta(minutes=2)).timestamp() * 1000),
            is_ojt=True,
            teacher_user_id=member_id,
        )


def test_start_timer_switch_records_previous_task_interval(tmp_path, monkeypatch) -> None:
    service, repository, member_id, first_task_id = _build_service(tmp_path)
    project = service.get_project()
    top = repository.get_user_by_name("top")
    assert top is not None
    second_task = repository.create_task(
        NewTaskInput(
            ProjectId=project.Id,
            ParentTaskId=None,
            Name="second task",
            Type="Task",
            AssigneeUserId=member_id,
            CreatorUserId=top.Id,
            StartDate=date(2026, 4, 10),
            EndDate=date(2026, 4, 10),
            AssignedManHour=120,
            PlannedDeliverables=[],
        )
    )

    class FixedDateTime(datetime):
        current = datetime(2026, 4, 10, 9, 0, 0)

        @classmethod
        def now(cls):
            return cls.current

    monkeypatch.setattr(core_module, "datetime", FixedDateTime)

    service.start_timer(
        user_id=member_id,
        task_id=first_task_id,
        is_ojt=False,
        teacher_user_id=None,
    )
    FixedDateTime.current = datetime(2026, 4, 10, 9, 20, 0)
    service.start_timer(
        user_id=member_id,
        task_id=second_task.Id,
        is_ojt=False,
        teacher_user_id=None,
    )

    raw_worklogs = repository.list_raw_worklogs_by_user_and_day(
        user_id=member_id,
        work_date=date(2026, 4, 10),
    )
    active = repository.get_active_timer(member_id)
    assert len(raw_worklogs) == 1
    assert raw_worklogs[0].TaskId == first_task_id
    assert raw_worklogs[0].DurationSeconds == 20 * 60
    assert active is not None
    assert active.TaskId == second_task.Id
