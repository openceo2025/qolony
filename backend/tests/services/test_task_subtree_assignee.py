from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.tms_core.config import AppConfig, CalendarConfig, IQuavisConfig, Settings, TeamConfig, UsersConfig
from app.tms_core.db.repository import NewTaskInput, TmsRepository
from app.tms_core.db.session import create_all_tables, create_session_factory, create_sqlite_engine
from app.tms_core.services.core import TmsService, UserInputError


def _build(tmp_path):
    today = date.today()
    db_path = tmp_path / "subtree_assignee.db"
    engine = create_sqlite_engine(db_path)
    create_all_tables(engine)
    repository = TmsRepository(create_session_factory(engine))
    repository.ensure_users(
        names=["山田", "鈴木", "田中"],
        top_manager_name="山田",
        free_time_rate_default=0.2,
    )
    project = repository.ensure_single_project(
        name="QOLONY",
        start_date=today - timedelta(days=30),
        end_date=today + timedelta(days=365),
        crash_zone_pct=20.0,
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
            max_retries=1,
            retry_base_seconds=1,
            poll_interval_seconds=60,
            allowed_project_ids=[],
        ),
        team=TeamConfig(
            project_name="QOLONY",
            project_start_date=project.StartDate,
            project_end_date=project.EndDate,
            default_education_rate_monthly=0.1,
        ),
        users=UsersConfig(top_manager="山田", members=["山田", "鈴木", "田中"]),
        calendar=CalendarConfig(holidays=set()),
        initial_tasks=[],
        split_templates=[],
    )
    service = TmsService(settings=settings, repository=repository)
    top = repository.get_user_by_name("山田")
    suzuki = repository.get_user_by_name("鈴木")
    tanaka = repository.get_user_by_name("田中")

    def add(name, parent_id, assignee, status="NotStart"):
        task = repository.create_task(
            NewTaskInput(
                ProjectId=project.Id,
                ParentTaskId=parent_id,
                Name=name,
                Type="GroupTask" if parent_id is None else "Task",
                AssigneeUserId=assignee.Id,
                CreatorUserId=top.Id,
                StartDate=today,
                EndDate=today + timedelta(days=5),
                AssignedManHour=120,
                PlannedDeliverables=[],
            )
        )
        if status != "NotStart":
            repository.update_task_status(task.Id, status, 100.0 if status == "Completion" else 0.0)
        return task

    root = add("ROOT", None, suzuki)
    tasks = {
        "root": root,
        "own": add("鈴木の未着手", root.Id, suzuki),
        "other": add("山田の未着手", root.Id, top),
        "done": add("鈴木の完了", root.Id, suzuki, status="Completion"),
        "abort": add("鈴木の中止", root.Id, suzuki, status="Abort"),
    }
    return service, repository, tasks, {"top": top, "suzuki": suzuki, "tanaka": tanaka}


def test_top_manager_overwrites_every_target_task(tmp_path) -> None:
    service, repository, tasks, users = _build(tmp_path)

    result = service.reassign_task_subtree_assignee(
        root_task_id=tasks["root"].Id,
        assignee_user_id=users["tanaka"].Id,
        operator_user_id=users["top"].Id,
    )

    # 他人担当でも例外なく上書きする。
    assert repository.get_task(tasks["root"].Id).AssigneeUserId == users["tanaka"].Id
    assert repository.get_task(tasks["own"].Id).AssigneeUserId == users["tanaka"].Id
    assert repository.get_task(tasks["other"].Id).AssigneeUserId == users["tanaka"].Id
    assert result["skipped_not_owned"] == 0
    # root / own / other の3件。完了・中止は対象外。
    assert result["changed"] == 3
    assert result["unchanged"] == 0


def test_completed_and_aborted_tasks_are_never_reassigned(tmp_path) -> None:
    # 終わった仕事の担当を後からすり替えると、実績と担当の対応が崩れる。
    service, repository, tasks, users = _build(tmp_path)

    result = service.reassign_task_subtree_assignee(
        root_task_id=tasks["root"].Id,
        assignee_user_id=users["tanaka"].Id,
        operator_user_id=users["top"].Id,
    )

    assert repository.get_task(tasks["done"].Id).AssigneeUserId == users["suzuki"].Id
    assert repository.get_task(tasks["abort"].Id).AssigneeUserId == users["suzuki"].Id
    assert result["skipped_completed"] == 1
    assert result["skipped_aborted"] == 1


def test_general_user_only_reassigns_their_own_tasks(tmp_path) -> None:
    service, repository, tasks, users = _build(tmp_path)

    result = service.reassign_task_subtree_assignee(
        root_task_id=tasks["root"].Id,
        assignee_user_id=users["tanaka"].Id,
        operator_user_id=users["suzuki"].Id,
    )

    # 自分が担当のものだけ渡せる。
    assert repository.get_task(tasks["root"].Id).AssigneeUserId == users["tanaka"].Id
    assert repository.get_task(tasks["own"].Id).AssigneeUserId == users["tanaka"].Id
    # 他人担当のタスクは触らない。エラーにせず対象外にして、他は通す。
    assert repository.get_task(tasks["other"].Id).AssigneeUserId == users["top"].Id
    assert result["skipped_not_owned"] == 1
    assert result["changed"] == 2


def test_general_user_cannot_take_over_someone_elses_task(tmp_path) -> None:
    service, repository, tasks, users = _build(tmp_path)

    # 田中が「鈴木担当のサブツリー」を自分へ寄せようとしても、自分担当分しか動かない。
    result = service.reassign_task_subtree_assignee(
        root_task_id=tasks["root"].Id,
        assignee_user_id=users["tanaka"].Id,
        operator_user_id=users["tanaka"].Id,
    )

    assert repository.get_task(tasks["root"].Id).AssigneeUserId == users["suzuki"].Id
    assert repository.get_task(tasks["own"].Id).AssigneeUserId == users["suzuki"].Id
    assert result["changed"] == 0
    assert result["skipped_not_owned"] == 3


def test_only_the_selected_subtree_is_touched(tmp_path) -> None:
    service, repository, tasks, users = _build(tmp_path)
    today = date.today()
    project = service.get_project()
    outside = repository.create_task(
        NewTaskInput(
            ProjectId=project.Id,
            ParentTaskId=None,
            Name="別ツリー",
            Type="Task",
            AssigneeUserId=users["suzuki"].Id,
            CreatorUserId=users["top"].Id,
            StartDate=today,
            EndDate=today + timedelta(days=5),
            AssignedManHour=60,
            PlannedDeliverables=[],
        )
    )

    service.reassign_task_subtree_assignee(
        root_task_id=tasks["root"].Id,
        assignee_user_id=users["tanaka"].Id,
        operator_user_id=users["top"].Id,
    )

    assert repository.get_task(outside.Id).AssigneeUserId == users["suzuki"].Id


def test_unknown_assignee_or_task_is_rejected(tmp_path) -> None:
    service, _repository, tasks, users = _build(tmp_path)

    with pytest.raises(UserInputError):
        service.reassign_task_subtree_assignee(
            root_task_id=tasks["root"].Id,
            assignee_user_id="MISSING",
            operator_user_id=users["top"].Id,
        )
    with pytest.raises(UserInputError):
        service.reassign_task_subtree_assignee(
            root_task_id="MISSING",
            assignee_user_id=users["tanaka"].Id,
            operator_user_id=users["top"].Id,
        )
