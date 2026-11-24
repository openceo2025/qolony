from __future__ import annotations

from datetime import date, timedelta

from app.iquavis.editor_models import IquavisEditorSnapshot, IquavisTaskDomainRecord, IquavisTaskRecord, SyncedIquavisTarget
from app.iquavis.sync import IQuavisSyncService
from app.tms_core.config import AppConfig, CalendarConfig, IQuavisConfig, Settings, TeamConfig, UsersConfig
from app.tms_core.db.repository import NewTaskInput, TmsRepository
from app.tms_core.db.session import create_all_tables, create_session_factory, create_sqlite_engine
from app.tms_core.services.core import TmsService


def _build_service(tmp_path):
    today = date.today()
    db_path = tmp_path / "sync_exclusion.db"
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
        start_date=today - timedelta(days=10),
        end_date=today + timedelta(days=60),
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
            enabled=True,
            base_url="http://example.invalid/iquavis-api",
            username="u0001",
            password="u0001",
            timeout_seconds=10.0,
            max_retries=5,
            retry_base_seconds=30,
            poll_interval_seconds=60,
            allowed_project_ids=[],
        ),
        team=TeamConfig(
            project_name="QOLONY",
            project_start_date=project.StartDate,
            project_end_date=project.EndDate,
            default_education_rate_monthly=0.1,
        ),
        users=UsersConfig(top_manager="top", members=["top", "member"]),
        calendar=CalendarConfig(holidays=set()),
        initial_tasks=[],
        split_templates=[],
    )
    return TmsService(settings=settings, repository=repository), repository, project


def _create_linked_task_tree(repository: TmsRepository, project_id: str):
    top = repository.get_top_manager()
    assert top is not None
    today = date.today()
    root = repository.create_task(
        NewTaskInput(
            ProjectId=project_id,
            ParentTaskId=None,
            Name="Domain Root",
            Type="TaskDomain",
            AssigneeUserId=top.Id,
            CreatorUserId=top.Id,
            StartDate=today,
            EndDate=today + timedelta(days=30),
            AssignedManHour=240,
            PlannedDeliverables=[],
        )
    )
    child = repository.create_task(
        NewTaskInput(
            ProjectId=project_id,
            ParentTaskId=root.Id,
            Name="Imported Task",
            Type="Task",
            AssigneeUserId=top.Id,
            CreatorUserId=top.Id,
            StartDate=today,
            EndDate=today + timedelta(days=10),
            AssignedManHour=120,
            PlannedDeliverables=[],
        )
    )
    repository.set_task_iquavis_link(root.Id, iquavis_task_id="DOMAIN-1", iquavis_project_id="P1")
    repository.set_task_iquavis_link(child.Id, iquavis_task_id="TASK-1", iquavis_project_id="P1")
    return top, root, child


def test_root_sync_exclusion_marks_entire_subtree_and_restores(tmp_path) -> None:
    service, repository, project = _build_service(tmp_path)
    top, root, child = _create_linked_task_tree(repository, project.Id)

    before = {task.Id: task for task in service.list_tasks()}
    assert before[root.Id].IQuavisSyncState == "Linked"
    assert before[child.Id].IQuavisSyncState == "Linked"

    result = service.set_iquavis_sync_exclusion(root_task_id=root.Id, operator_user_id=top.Id, excluded=True)

    assert result["affected_count"] == 2
    excluded = {task.Id: task for task in service.list_tasks()}
    assert excluded[root.Id].IQuavisSyncState == "Excluded"
    assert excluded[child.Id].IQuavisSyncState == "Excluded"
    assert excluded[child.Id].IQuavisSyncExcluded is True

    service.set_iquavis_sync_exclusion(root_task_id=root.Id, operator_user_id=top.Id, excluded=False)

    restored = {task.Id: task for task in service.list_tasks()}
    assert restored[root.Id].IQuavisSyncState == "Linked"
    assert restored[child.Id].IQuavisSyncState == "Linked"
    assert restored[child.Id].IQuavisSyncExcluded is False


def test_excluded_subtree_does_not_enqueue_iquavis_sync_jobs(tmp_path) -> None:
    service, repository, project = _build_service(tmp_path)
    top, root, child = _create_linked_task_tree(repository, project.Id)
    sync_service = IQuavisSyncService(repository=repository, tms_service=service)

    assert sync_service.queue_task_progress_sync(child.Id) is True
    service.set_iquavis_sync_exclusion(root_task_id=root.Id, operator_user_id=top.Id, excluded=True)

    assert sync_service.queue_task_progress_sync(child.Id) is False
    assert sync_service.queue_task_schedule_sync(root.Id) is False


def test_sync_exclusion_can_target_non_root_task_subtree(tmp_path) -> None:
    service, repository, project = _build_service(tmp_path)
    top, _root, child = _create_linked_task_tree(repository, project.Id)

    service.set_iquavis_sync_exclusions(root_task_ids=[child.Id], operator_user_id=top.Id, excluded=True)

    listed = {task.Id: task for task in service.list_tasks()}
    assert listed[child.Id].IQuavisSyncState == "Excluded"


def test_excluded_root_filters_new_remote_descendants_from_editor_snapshot(tmp_path) -> None:
    service, repository, project = _build_service(tmp_path)
    top, root, _child = _create_linked_task_tree(repository, project.Id)
    service.set_iquavis_sync_exclusion(root_task_id=root.Id, operator_user_id=top.Id, excluded=True)

    today = date.today()
    snapshot = IquavisEditorSnapshot(
        synced_targets=[
            SyncedIquavisTarget(iquavis_id="DOMAIN-1", kind="task_domain"),
            SyncedIquavisTarget(iquavis_id="DOMAIN-NEW", kind="task_domain"),
            SyncedIquavisTarget(iquavis_id="TASK-NEW", kind="task"),
        ],
        task_domains=[
            IquavisTaskDomainRecord(
                iquavis_task_domain_id="DOMAIN-NEW",
                name="New child domain",
                project_id="P1",
                parent_task_domain_id="DOMAIN-1",
                start_date=today,
                end_date=today + timedelta(days=20),
            )
        ],
        tasks=[
            IquavisTaskRecord(
                iquavis_task_id="TASK-NEW",
                name="New task under excluded domain",
                project_id="P1",
                task_domain_id="DOMAIN-NEW",
                parent_task_id=None,
                start_date=today,
                end_date=today + timedelta(days=10),
            )
        ],
    )

    filtered = IQuavisSyncService(repository=repository, tms_service=service)._without_excluded_targets(snapshot)

    assert filtered.synced_targets == []
