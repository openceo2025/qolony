from __future__ import annotations

from datetime import date, timedelta

from app.iquavis.editor_import import IquavisEditorImportService
from app.iquavis.editor_models import (
    IquavisAssigneeRecord,
    IquavisEditorSnapshot,
    IquavisTaskDomainRecord,
    IquavisTaskRecord,
    SyncedIquavisTarget,
)
from app.tms_core.config import AppConfig, CalendarConfig, IQuavisConfig, Settings, TeamConfig, UsersConfig
from app.tms_core.db.repository import NewTaskInput, TmsRepository
from app.tms_core.db.session import create_all_tables, create_session_factory, create_sqlite_engine
from app.tms_core.services.core import TmsService


def _build_service(tmp_path):
    today = date.today()
    db_path = tmp_path / "editor_import.db"
    engine = create_sqlite_engine(db_path)
    create_all_tables(engine)
    repository = TmsRepository(create_session_factory(engine))
    repository.ensure_users(
        names=["top", "member"],
        top_manager_name="top",
        free_time_rate_default=0.2,
        login_id_by_name={"member": "U0002"},
        iquavis_user_id_by_name={"member": "USER-1"},
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
            enabled=True,
            base_url="http://example.invalid/iquavis-api",
            username="u0001",
            password="u0001",
            timeout_seconds=10.0,
            max_retries=5,
            retry_base_seconds=30,
            poll_interval_seconds=60,
            allowed_project_ids=["P1"],
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
    service = TmsService(settings=settings, repository=repository)
    return service, repository


def test_editor_import_creates_missing_domain_chain_and_task(tmp_path) -> None:
    service, repository = _build_service(tmp_path)
    today = date.today()
    snapshot = IquavisEditorSnapshot(
        synced_targets=[SyncedIquavisTarget(iquavis_id="TASK-1")],
        task_domains=[
            IquavisTaskDomainRecord(
                iquavis_task_domain_id="DOMAIN-ROOT",
                name="Root Domain",
                project_id="P1",
                parent_task_domain_id=None,
                start_date=today,
                end_date=today + timedelta(days=30),
            ),
            IquavisTaskDomainRecord(
                iquavis_task_domain_id="DOMAIN-CHILD",
                name="Child Domain",
                project_id="P1",
                parent_task_domain_id="DOMAIN-ROOT",
                start_date=today,
                end_date=today + timedelta(days=20),
            ),
        ],
        tasks=[
            IquavisTaskRecord(
                iquavis_task_id="TASK-1",
                name="Imported Task",
                project_id="P1",
                task_domain_id="DOMAIN-CHILD",
                parent_task_id=None,
                start_date=today,
                end_date=today + timedelta(days=5),
                assigned_minutes=45,
                status="DuringStart",
                progress_rate=25,
                assignee=IquavisAssigneeRecord(iquavis_user_id="USER-1"),
            )
        ],
    )

    result = IquavisEditorImportService(
        repository=repository,
        tms_service=service,
    ).import_new_tasks_from_snapshot(snapshot)

    assert len(result.imported_task_domain_ids) == 2
    assert len(result.imported_task_ids) == 1
    root = repository.get_task_by_iquavis_task_id("DOMAIN-ROOT")
    child = repository.get_task_by_iquavis_task_id("DOMAIN-CHILD")
    task = repository.get_task_by_iquavis_task_id("TASK-1")
    assert root is not None and child is not None and task is not None
    assert root.ParentTaskId is None
    assert child.ParentTaskId == root.Id
    assert task.ParentTaskId == child.Id
    assert task.AssignedManHour == 0
    assert task.Status == "DuringStart"
    assert task.ProgressRate == 0


def test_editor_import_uses_all_exported_tasks_when_targets_are_auto_completed(tmp_path) -> None:
    service, repository = _build_service(tmp_path)
    today = date.today()
    snapshot = IquavisEditorSnapshot(
        synced_targets=[
            SyncedIquavisTarget(iquavis_id="NEW-DOMAIN", kind="task_domain"),
            SyncedIquavisTarget(iquavis_id="NEW-TASK", kind="task"),
        ],
        task_domains=[
            IquavisTaskDomainRecord(
                iquavis_task_domain_id="NEW-DOMAIN",
                name="VC74_B-Gr_550_幹業務_ver2",
                project_id="P1",
                parent_task_domain_id=None,
                start_date=today,
                end_date=today + timedelta(days=30),
            )
        ],
        tasks=[
            IquavisTaskRecord(
                iquavis_task_id="NEW-TASK",
                name="新規プロジェクト配下タスク",
                project_id="P1",
                task_domain_id="NEW-DOMAIN",
                parent_task_id=None,
                start_date=today,
                end_date=today + timedelta(days=3),
            )
        ],
    )

    result = IquavisEditorImportService(repository=repository, tms_service=service).import_new_tasks_from_snapshot(
        snapshot
    )

    assert len(result.imported_task_domain_ids) == 1
    assert len(result.imported_task_ids) == 1
    domain = repository.get_task_by_iquavis_task_id("NEW-DOMAIN")
    task = repository.get_task_by_iquavis_task_id("NEW-TASK")
    assert domain is not None and task is not None
    assert domain.Name == "VC74_B-Gr_550_幹業務_ver2"
    assert task.ParentTaskId == domain.Id


def test_editor_import_skips_task_when_task_domain_is_missing(tmp_path) -> None:
    service, repository = _build_service(tmp_path)
    today = date.today()
    snapshot = IquavisEditorSnapshot(
        synced_targets=[SyncedIquavisTarget(iquavis_id="ORPHAN-TASK", kind="task")],
        task_domains=[],
        tasks=[
            IquavisTaskRecord(
                iquavis_task_id="ORPHAN-TASK",
                name="PP31",
                project_id="P1",
                task_domain_id="MISSING-DOMAIN",
                parent_task_id=None,
                start_date=today,
                end_date=today,
                task_type="GroupTask",
            )
        ],
    )

    result = IquavisEditorImportService(repository=repository, tms_service=service).import_new_tasks_from_snapshot(snapshot)

    assert result.imported_task_ids == []
    assert result.missing_target_ids == ["ORPHAN-TASK"]
    assert repository.get_task_by_iquavis_task_id("ORPHAN-TASK") is None


def test_editor_import_inherits_parent_assignee_when_assignee_is_blank(tmp_path) -> None:
    service, repository = _build_service(tmp_path)
    project = service.get_project()
    today = date.today()
    top = repository.get_user_by_name("top")
    member = repository.get_user_by_name("member")
    assert top is not None and member is not None
    local_parent = repository.create_task(
        NewTaskInput(
            ProjectId=project.Id,
            ParentTaskId=None,
            Name="Parent",
            Type="GroupTask",
            AssigneeUserId=member.Id,
            CreatorUserId=top.Id,
            StartDate=today,
            EndDate=today + timedelta(days=10),
            AssignedManHour=0,
            PlannedDeliverables=[],
        )
    )
    snapshot = IquavisEditorSnapshot(
        synced_targets=[
            SyncedIquavisTarget(
                iquavis_id="TASK-INHERIT",
                qolony_parent_task_id=local_parent.Id,
            )
        ],
        tasks=[
            IquavisTaskRecord(
                iquavis_task_id="TASK-INHERIT",
                name="Parent Fallback",
                project_id="P1",
                task_domain_id=None,
                parent_task_id=None,
                start_date=today,
                end_date=today + timedelta(days=5),
                assignee=None,
                creator=IquavisAssigneeRecord(name="top"),
            )
        ],
    )

    IquavisEditorImportService(
        repository=repository,
        tms_service=service,
    ).import_new_tasks_from_snapshot(snapshot, creator_user_name="top", default_assignee_name="top")

    task = repository.get_task_by_iquavis_task_id("TASK-INHERIT")
    assert task is not None
    assert task.AssigneeUserId == member.Id


def test_editor_import_prefers_official_assignee_over_parent_assignee(tmp_path) -> None:
    service, repository = _build_service(tmp_path)
    project = service.get_project()
    today = date.today()
    top = repository.get_user_by_name("top")
    member = repository.get_user_by_name("member")
    assert top is not None and member is not None
    local_parent = repository.create_task(
        NewTaskInput(
            ProjectId=project.Id,
            ParentTaskId=None,
            Name="Parent",
            Type="GroupTask",
            AssigneeUserId=top.Id,
            CreatorUserId=top.Id,
            StartDate=today,
            EndDate=today + timedelta(days=10),
            AssignedManHour=0,
            PlannedDeliverables=[],
        )
    )
    snapshot = IquavisEditorSnapshot(
        synced_targets=[
            SyncedIquavisTarget(
                iquavis_id="TASK-OFFICIAL",
                qolony_parent_task_id=local_parent.Id,
            )
        ],
        tasks=[
            IquavisTaskRecord(
                iquavis_task_id="TASK-OFFICIAL",
                name="Official Assignee",
                project_id="P1",
                task_domain_id=None,
                parent_task_id=None,
                start_date=today,
                end_date=today + timedelta(days=5),
                assignee=IquavisAssigneeRecord(iquavis_user_id="USER-1"),
            )
        ],
    )

    IquavisEditorImportService(repository=repository, tms_service=service).import_new_tasks_from_snapshot(
        snapshot,
        creator_user_name="top",
        default_assignee_name="top",
    )

    task = repository.get_task_by_iquavis_task_id("TASK-OFFICIAL")
    assert task is not None
    assert task.AssigneeUserId == member.Id


def test_editor_import_falls_back_to_admin_when_parent_assignee_is_inactive(tmp_path) -> None:
    service, repository = _build_service(tmp_path)
    project = service.get_project()
    today = date.today()
    top = repository.get_user_by_name("top")
    member = repository.get_user_by_name("member")
    assert top is not None and member is not None
    local_parent = repository.create_task(
        NewTaskInput(
            ProjectId=project.Id,
            ParentTaskId=None,
            Name="Parent",
            Type="GroupTask",
            AssigneeUserId=member.Id,
            CreatorUserId=top.Id,
            StartDate=today,
            EndDate=today + timedelta(days=10),
            AssignedManHour=0,
            PlannedDeliverables=[],
        )
    )
    repository.update_member_identity(user_id=member.Id, nickname=member.Nickname, is_active=False)
    snapshot = IquavisEditorSnapshot(
        synced_targets=[
            SyncedIquavisTarget(
                iquavis_id="TASK-INACTIVE-PARENT",
                qolony_parent_task_id=local_parent.Id,
            )
        ],
        tasks=[
            IquavisTaskRecord(
                iquavis_task_id="TASK-INACTIVE-PARENT",
                name="Inactive Parent Fallback",
                project_id="P1",
                task_domain_id=None,
                parent_task_id=None,
                start_date=today,
                end_date=today + timedelta(days=5),
                assignee=None,
            )
        ],
    )

    IquavisEditorImportService(repository=repository, tms_service=service).import_new_tasks_from_snapshot(
        snapshot,
        creator_user_name="top",
        default_assignee_name="top",
    )

    task = repository.get_task_by_iquavis_task_id("TASK-INACTIVE-PARENT")
    assert task is not None
    assert task.AssigneeUserId == top.Id


def test_editor_import_keeps_imported_child_estimates_until_explicit_assignment(tmp_path) -> None:
    service, repository = _build_service(tmp_path)
    top = repository.get_user_by_name("top")
    assert top is not None
    today = date.today()
    snapshot = IquavisEditorSnapshot(
        synced_targets=[
            SyncedIquavisTarget(iquavis_id="TASK-A"),
            SyncedIquavisTarget(iquavis_id="TASK-B"),
        ],
        task_domains=[
            IquavisTaskDomainRecord(
                iquavis_task_domain_id="DOMAIN-ROOT",
                name="Root Domain",
                project_id="P1",
                parent_task_domain_id=None,
                start_date=today,
                end_date=today + timedelta(days=30),
            ),
        ],
        tasks=[
            IquavisTaskRecord(
                iquavis_task_id="TASK-A",
                name="A",
                project_id="P1",
                task_domain_id="DOMAIN-ROOT",
                parent_task_id=None,
                start_date=today,
                end_date=today + timedelta(days=5),
                assigned_minutes=30,
            ),
            IquavisTaskRecord(
                iquavis_task_id="TASK-B",
                name="B",
                project_id="P1",
                task_domain_id="DOMAIN-ROOT",
                parent_task_id=None,
                start_date=today,
                end_date=today + timedelta(days=6),
                assigned_minutes=300,
            ),
        ],
    )

    IquavisEditorImportService(repository=repository, tms_service=service).import_new_tasks_from_snapshot(snapshot)
    root = repository.get_task_by_iquavis_task_id("DOMAIN-ROOT")
    task_a = repository.get_task_by_iquavis_task_id("TASK-A")
    task_b = repository.get_task_by_iquavis_task_id("TASK-B")
    assert root is not None and task_a is not None and task_b is not None
    assert task_a.AssignedManHour == 0
    assert task_b.AssignedManHour == 0

    service.set_root_task_assigned_manhour(
        task_id=root.Id,
        assigned_manhour=600,
        operator_user_id=top.Id,
    )

    root = repository.get_task_by_iquavis_task_id("DOMAIN-ROOT")
    task_a = repository.get_task_by_iquavis_task_id("TASK-A")
    task_b = repository.get_task_by_iquavis_task_id("TASK-B")
    assert root is not None and task_a is not None and task_b is not None
    assert root.AssignedManHour == 600
    assert task_a.AssignedManHour == 0
    assert task_b.AssignedManHour == 0


def test_editor_import_orders_linked_parent_task_before_child(tmp_path) -> None:
    service, repository = _build_service(tmp_path)
    today = date.today()
    snapshot = IquavisEditorSnapshot(
        synced_targets=[
            SyncedIquavisTarget(iquavis_id="TASK-CHILD"),
            SyncedIquavisTarget(iquavis_id="TASK-PARENT"),
        ],
        tasks=[
            IquavisTaskRecord(
                iquavis_task_id="TASK-CHILD",
                name="Child",
                project_id="P1",
                task_domain_id=None,
                parent_task_id="TASK-PARENT",
                start_date=today,
                end_date=today + timedelta(days=5),
            ),
            IquavisTaskRecord(
                iquavis_task_id="TASK-PARENT",
                name="Parent",
                project_id="P1",
                task_domain_id=None,
                parent_task_id=None,
                start_date=today,
                end_date=today + timedelta(days=10),
            ),
        ],
    )

    IquavisEditorImportService(repository=repository, tms_service=service).import_new_tasks_from_snapshot(snapshot)

    parent = repository.get_task_by_iquavis_task_id("TASK-PARENT")
    child = repository.get_task_by_iquavis_task_id("TASK-CHILD")
    assert parent is not None and child is not None
    assert child.ParentTaskId == parent.Id


def test_editor_import_explicit_qolony_parent_overrides_iquavis_parent_without_breaking_link(tmp_path) -> None:
    service, repository = _build_service(tmp_path)
    project = service.get_project()
    top = repository.get_top_manager()
    assert top is not None
    today = date.today()
    local_parent = repository.create_task(
        NewTaskInput(
            ProjectId=project.Id,
            ParentTaskId=None,
            Name="QOLONY Parent",
            Type="GroupTask",
            AssigneeUserId=top.Id,
            CreatorUserId=top.Id,
            StartDate=today,
            EndDate=today + timedelta(days=20),
            AssignedManHour=0,
            PlannedDeliverables=[],
        )
    )
    snapshot = IquavisEditorSnapshot(
        synced_targets=[
            SyncedIquavisTarget(
                iquavis_id="TASK-1",
                qolony_parent_task_id=local_parent.Id,
            )
        ],
        tasks=[
            IquavisTaskRecord(
                iquavis_task_id="TASK-1",
                name="Imported Task",
                project_id="P1",
                task_domain_id=None,
                parent_task_id=None,
                start_date=today,
                end_date=today + timedelta(days=5),
            )
        ],
    )

    IquavisEditorImportService(repository=repository, tms_service=service).import_new_tasks_from_snapshot(snapshot)

    task = repository.get_task_by_iquavis_task_id("TASK-1")
    assert task is not None
    assert task.ParentTaskId == local_parent.Id
    assert task.IQuavisTaskId == "TASK-1"


def test_editor_import_is_idempotent_for_existing_iquavis_ids(tmp_path) -> None:
    service, repository = _build_service(tmp_path)
    today = date.today()
    snapshot = IquavisEditorSnapshot(
        synced_targets=[SyncedIquavisTarget(iquavis_id="TASK-1")],
        task_domains=[
            IquavisTaskDomainRecord(
                iquavis_task_domain_id="DOMAIN-1",
                name="Domain",
                project_id="P1",
                parent_task_domain_id=None,
                start_date=today,
                end_date=today + timedelta(days=10),
            )
        ],
        tasks=[
            IquavisTaskRecord(
                iquavis_task_id="TASK-1",
                name="Imported Task",
                project_id="P1",
                task_domain_id="DOMAIN-1",
                parent_task_id=None,
                start_date=today,
                end_date=today + timedelta(days=5),
            )
        ],
    )
    importer = IquavisEditorImportService(repository=repository, tms_service=service)

    first = importer.import_new_tasks_from_snapshot(snapshot)
    second = importer.import_new_tasks_from_snapshot(snapshot)

    assert len(first.imported_task_domain_ids) == 1
    assert len(first.imported_task_ids) == 1
    assert second.imported_task_domain_ids == []
    assert second.imported_task_ids == []
    assert second.skipped_existing_ids == ["DOMAIN-1", "TASK-1"]


def test_editor_import_reports_missing_sync_targets(tmp_path) -> None:
    service, repository = _build_service(tmp_path)
    snapshot = IquavisEditorSnapshot(
        synced_targets=[SyncedIquavisTarget(iquavis_id="MISSING-1")],
        task_domains=[],
        tasks=[],
    )

    result = IquavisEditorImportService(
        repository=repository,
        tms_service=service,
    ).import_new_tasks_from_snapshot(snapshot)

    assert result.missing_target_ids == ["MISSING-1"]
    assert result.imported_task_domain_ids == []
    assert result.imported_task_ids == []


def test_editor_import_creates_ancestors_for_explicit_domain_target(tmp_path) -> None:
    service, repository = _build_service(tmp_path)
    today = date.today()
    snapshot = IquavisEditorSnapshot(
        synced_targets=[SyncedIquavisTarget(iquavis_id="DOMAIN-CHILD", kind="task_domain")],
        task_domains=[
            IquavisTaskDomainRecord(
                iquavis_task_domain_id="DOMAIN-ROOT",
                name="Root Domain",
                project_id="P1",
                parent_task_domain_id=None,
                start_date=today,
                end_date=today + timedelta(days=30),
            ),
            IquavisTaskDomainRecord(
                iquavis_task_domain_id="DOMAIN-CHILD",
                name="Child Domain",
                project_id="P1",
                parent_task_domain_id="DOMAIN-ROOT",
                start_date=today,
                end_date=today + timedelta(days=20),
            ),
        ],
        tasks=[],
    )

    result = IquavisEditorImportService(
        repository=repository,
        tms_service=service,
    ).import_new_tasks_from_snapshot(snapshot)

    root = repository.get_task_by_iquavis_task_id("DOMAIN-ROOT")
    child = repository.get_task_by_iquavis_task_id("DOMAIN-CHILD")
    assert len(result.imported_task_domain_ids) == 2
    assert root is not None and child is not None
    assert child.ParentTaskId == root.Id


def test_editor_import_uses_existing_domain_parent(tmp_path) -> None:
    service, repository = _build_service(tmp_path)
    project = service.get_project()
    top = repository.get_top_manager()
    assert top is not None
    today = date.today()
    existing_domain = repository.create_task(
        NewTaskInput(
            ProjectId=project.Id,
            ParentTaskId=None,
            Name="Existing Domain",
            Type="GroupTask",
            AssigneeUserId=top.Id,
            CreatorUserId=top.Id,
            StartDate=today,
            EndDate=today + timedelta(days=10),
            AssignedManHour=0,
            PlannedDeliverables=[],
        )
    )
    repository.set_task_iquavis_link(
        existing_domain.Id,
        iquavis_task_id="DOMAIN-1",
        iquavis_project_id="P1",
    )
    snapshot = IquavisEditorSnapshot(
        synced_targets=[SyncedIquavisTarget(iquavis_id="TASK-1")],
        task_domains=[
            IquavisTaskDomainRecord(
                iquavis_task_domain_id="DOMAIN-1",
                name="Domain",
                project_id="P1",
                parent_task_domain_id=None,
                start_date=today,
                end_date=today + timedelta(days=10),
            )
        ],
        tasks=[
            IquavisTaskRecord(
                iquavis_task_id="TASK-1",
                name="Imported Task",
                project_id="P1",
                task_domain_id="DOMAIN-1",
                parent_task_id=None,
                start_date=today,
                end_date=today + timedelta(days=5),
            )
        ],
    )

    result = IquavisEditorImportService(
        repository=repository,
        tms_service=service,
    ).import_new_tasks_from_snapshot(snapshot)

    task = repository.get_task_by_iquavis_task_id("TASK-1")
    assert result.imported_task_domain_ids == []
    assert len(result.imported_task_ids) == 1
    assert task is not None
    assert task.ParentTaskId == existing_domain.Id
