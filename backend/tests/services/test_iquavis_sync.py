from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook

from app.iquavis.client import IQuavisApiError, IQuavisRemoteTask
from app.iquavis.sync import IQuavisSyncService
from app.iquavis.xlsm_import import SELECTION_MARK, load_selected_tasks_from_xlsm
from app.tms_core.config import AppConfig, CalendarConfig, IQuavisConfig, Settings, TeamConfig, UsersConfig
from app.tms_core.db.repository import NewTaskInput, TmsRepository
from app.tms_core.db.session import create_all_tables, create_session_factory, create_sqlite_engine
from app.tms_core.services.core import TmsService


class FakeIQuavisClient:
    def __init__(self, remote_tasks: dict[str, IQuavisRemoteTask]):
        self.remote_tasks = remote_tasks
        self.schedule_updates: list[tuple[str, date, date]] = []
        self.progress_updates: list[tuple[str, str, int | None]] = []
        self.reject_progress_with_rate_once = False

    def close(self) -> None:
        return None

    def get_task(self, task_id: str, *, include_progress: bool = True, include_assigns: bool = False) -> IQuavisRemoteTask:
        return self.remote_tasks[task_id]

    def put_task_schedule(self, task_id: str, *, start_date: date, end_date: date) -> None:
        self.schedule_updates.append((task_id, start_date, end_date))

    def put_task_progress(self, task_id: str, *, status: str, progress_rate: int | None) -> None:
        self.progress_updates.append((task_id, status, progress_rate))
        if self.reject_progress_with_rate_once and progress_rate is not None:
            self.reject_progress_with_rate_once = False
            raise IQuavisApiError("manual progress not allowed", status_code=400)


def _build_service(tmp_path):
    today = date.today()
    project_start = today - timedelta(days=30)
    project_end = today + timedelta(days=365)

    db_path = tmp_path / "iquavis_sync.db"
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

    task = repository.create_task(
        NewTaskInput(
            ProjectId=project.Id,
            ParentTaskId=None,
            Name="paired-task",
            Type="Task",
            AssigneeUserId=member.Id,
            CreatorUserId=top.Id,
            StartDate=today,
            EndDate=today + timedelta(days=5),
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
            enabled=True,
            base_url="http://example.invalid/iquavis-api",
            username="u0001",
            password="u0001",
            timeout_seconds=10.0,
            max_retries=5,
            retry_base_seconds=30,
            poll_interval_seconds=60,
            allowed_project_ids=["P1", "P2"],
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
    return service, repository, top.Id, member.Id, task.Id


def _remote_task(*, task_id: str, parent_task_id: str | None = None, name: str = "remote-task") -> IQuavisRemoteTask:
    return IQuavisRemoteTask(
        id=task_id,
        project_id="P1",
        parent_task_id=parent_task_id,
        name=name,
        task_type="Task",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=7),
        note="remote memo",
        progress_type="ManualSetting",
        status="DuringStart",
        progress_rate=20,
        first_assign_iquavis_user_id=None,
        first_assign_login_id="member",
        first_assign_name="member",
    )


def test_schedule_update_enqueues_and_pushes_iquavis_job(tmp_path) -> None:
    service, repository, _, member_id, task_id = _build_service(tmp_path)
    remote = _remote_task(task_id="REMOTE-1")
    repository.set_task_iquavis_link(task_id, iquavis_task_id=remote.id, iquavis_project_id=remote.project_id)
    sync = IQuavisSyncService(
        repository=repository,
        tms_service=service,
        client=FakeIQuavisClient({remote.id: remote}),
    )

    new_start = date.today() + timedelta(days=1)
    new_end = date.today() + timedelta(days=8)
    service.update_task_schedule(task_id, start_date=new_start, end_date=new_end, operator_user_id=member_id)
    processed = sync.process_outbound_jobs()

    assert processed == 1
    assert sync.client.schedule_updates == [(remote.id, new_start, new_end)]


def test_list_tasks_exposes_iquavis_pairing_ids(tmp_path) -> None:
    service, repository, _, _, task_id = _build_service(tmp_path)
    remote = _remote_task(task_id="REMOTE-LIST")
    repository.set_task_iquavis_link(task_id, iquavis_task_id=remote.id, iquavis_project_id=remote.project_id)

    listed = {task.Id: task for task in service.list_tasks()}

    assert listed[task_id].IQuavisTaskId == remote.id
    assert listed[task_id].IQuavisProjectId == remote.project_id


def test_direct_api_disabled_does_not_enqueue_outbound_jobs(tmp_path) -> None:
    service, repository, _, _, task_id = _build_service(tmp_path)
    settings = replace(service.settings, iquavis=replace(service.settings.iquavis, enabled=False))
    disabled_service = TmsService(settings=settings, repository=repository)
    remote = _remote_task(task_id="REMOTE-DISABLED")
    repository.set_task_iquavis_link(task_id, iquavis_task_id=remote.id, iquavis_project_id=remote.project_id)
    sync = IQuavisSyncService(
        repository=repository,
        tms_service=disabled_service,
        client=FakeIQuavisClient({remote.id: remote}),
    )

    assert sync.queue_task_schedule_sync(task_id) is False
    assert sync.process_outbound_jobs() == 0


def test_progress_sync_falls_back_to_status_only_when_manual_progress_is_rejected(tmp_path) -> None:
    service, repository, _, member_id, task_id = _build_service(tmp_path)
    remote = _remote_task(task_id="REMOTE-2")
    repository.set_task_iquavis_link(task_id, iquavis_task_id=remote.id, iquavis_project_id=remote.project_id)
    fake_client = FakeIQuavisClient({remote.id: remote})
    fake_client.reject_progress_with_rate_once = True
    sync = IQuavisSyncService(repository=repository, tms_service=service, client=fake_client)

    service.complete_task(task_id, operator_user_id=member_id)
    processed = sync.process_outbound_jobs()

    assert processed == 1
    assert fake_client.progress_updates == [
        (remote.id, "Completion", 100),
        (remote.id, "Completion", None),
    ]


def test_pull_remote_updates_applies_schedule_and_completion_when_no_local_jobs_are_open(tmp_path) -> None:
    service, repository, _, _, task_id = _build_service(tmp_path)
    remote = replace(
        _remote_task(task_id="REMOTE-3"),
        start_date=date.today() + timedelta(days=2),
        end_date=date.today() + timedelta(days=9),
        status="Completion",
        progress_rate=100,
    )
    repository.set_task_iquavis_link(task_id, iquavis_task_id=remote.id, iquavis_project_id=remote.project_id)
    sync = IQuavisSyncService(
        repository=repository,
        tms_service=service,
        client=FakeIQuavisClient({remote.id: remote}),
    )

    pulled = sync.pull_remote_updates()
    updated = repository.get_task(task_id)

    assert pulled == 1
    assert updated is not None
    assert updated.StartDate == remote.start_date
    assert updated.EndDate == remote.end_date
    assert updated.Status == "Completion"


def test_import_remote_tasks_preserves_parent_child_relationship_only_when_parent_is_present(tmp_path) -> None:
    service, repository, _, _, _ = _build_service(tmp_path)
    remote_parent = replace(_remote_task(task_id="REMOTE-PARENT", name="parent"), task_type="GroupTask")
    remote_child = replace(_remote_task(task_id="REMOTE-CHILD", parent_task_id=remote_parent.id, name="child"))
    remote_orphan = replace(_remote_task(task_id="REMOTE-ORPHAN", parent_task_id="MISSING-PARENT", name="orphan"))
    sync = IQuavisSyncService(
        repository=repository,
        tms_service=service,
        client=FakeIQuavisClient(
            {
                remote_parent.id: remote_parent,
                remote_child.id: remote_child,
                remote_orphan.id: remote_orphan,
            }
        ),
    )

    local_ids = sync.import_remote_tasks(
        iquavis_task_ids=[remote_parent.id, remote_child.id, remote_orphan.id],
        creator_user_name="top",
        default_assignee_name="member",
    )
    imported = {task.IQuavisTaskId: task for task in repository.list_paired_tasks()}

    assert len(local_ids) == 3
    assert imported[remote_child.id].ParentTaskId == imported[remote_parent.id].Id
    assert imported[remote_orphan.id].ParentTaskId is None


def _write_xlsm_fixture(path: Path, *, local_parent_id: str) -> None:
    workbook = Workbook()
    personal = workbook.active
    personal.title = "551個人業務計画 (1)"
    personal.append(
        [
            "読み取り対象",
            "親タスクId",
            "Id",
            "Name",
            "Type",
            "StartDate",
            "EndDate",
            "ProjectId",
            "Assigns",
            "ParentTaskId",
            "Note",
            "Progress.Status",
            "Progress.ProgressRate",
        ]
    )
    personal.append(
        [
            "●",
            "",
            "REMOTE-PARENT-XLSM",
            "parent-from-xlsm",
            "GroupTask",
            "2026-04-01",
            "2026-04-30",
            "P1",
            '[{"LoginId":"member","Name":"member"}]',
            "",
            "memo-parent",
            "DuringStart",
            20,
        ]
    )
    personal.append(
        [
            "●",
            local_parent_id,
            "REMOTE-LOCAL-CHILD",
            "child-under-local-parent",
            "Task",
            "2026-04-03",
            "2026-04-10",
            "P1",
            '[{"LoginId":"member","Name":"member"}]',
            "MISSING-REMOTE",
            "memo-local",
            "NotStart",
            0,
        ]
    )

    overall = workbook.create_sheet("551_全体日程_20240401")
    overall.append(
        [
            "Id",
            "Name",
            "Type",
            "StartDate",
            "EndDate",
            "ProjectId",
            "Assigns",
            "読み取り対象",
            "親タスクId",
            "ParentTaskId",
            "Note",
            "Progress.Status",
            "Progress.ProgressRate",
        ]
    )
    overall.append(
        [
            "REMOTE-CHILD-XLSM",
            "child-from-xlsm",
            "Task",
            "2026-04-02",
            "2026-04-20",
            "P1",
            '[{"LoginId":"member","Name":"member"}]',
            "●",
            "",
            "REMOTE-PARENT-XLSM",
            "memo-child",
            "Completion",
            100,
        ]
    )
    overall.append(
        [
            "REMOTE-EXISTING",
            "already-imported-from-xlsm",
            "Task",
            "2026-04-04",
            "2026-04-11",
            "P1",
            '[{"LoginId":"member","Name":"member"}]',
            "●",
            "",
            "",
            "memo-existing",
            "DuringStart",
            40,
        ]
    )
    overall.append(
        [
            "REMOTE-PARENT-XLSM",
            "duplicate-parent",
            "GroupTask",
            "2026-04-01",
            "2026-04-30",
            "P1",
            '[{"LoginId":"member","Name":"member"}]',
            "●",
            "",
            "",
            "memo-duplicate",
            "DuringStart",
            50,
        ]
    )
    workbook.save(path)


def test_load_selected_tasks_from_xlsm_supports_multiple_sheet_layouts(tmp_path) -> None:
    workbook_path = tmp_path / "iquavis_editer.xlsm"
    _write_xlsm_fixture(workbook_path, local_parent_id="LOCAL-ROOT")

    selection = load_selected_tasks_from_xlsm(
        workbook_path=workbook_path,
        sheet_names=["551個人業務計画 (1)", "551_全体日程_20240401"],
    )

    assert selection.selected_count == 5
    assert selection.duplicate_count == 1
    assert [row.iquavis_task_id for row in selection.rows] == [
        "REMOTE-PARENT-XLSM",
        "REMOTE-LOCAL-CHILD",
        "REMOTE-CHILD-XLSM",
        "REMOTE-EXISTING",
    ]
    assert selection.rows[0].parent_override_local_task_id is None
    assert selection.rows[1].parent_override_local_task_id == "LOCAL-ROOT"
    assert selection.rows[2].remote_parent_task_id == "REMOTE-PARENT-XLSM"


def test_import_tasks_from_xlsm_creates_only_missing_tasks_and_resolves_parents(tmp_path) -> None:
    service, repository, top_id, member_id, _ = _build_service(tmp_path)
    top = repository.get_user(top_id)
    member = repository.get_user(member_id)
    assert top is not None and member is not None
    project = repository.get_project()
    assert project is not None

    local_parent = repository.create_task(
        NewTaskInput(
            ProjectId=project.Id,
            ParentTaskId=None,
            Name="local-parent",
            Type="GroupTask",
            AssigneeUserId=member.Id,
            CreatorUserId=top.Id,
            StartDate=date(2026, 4, 1),
            EndDate=date(2026, 4, 30),
            AssignedManHour=0,
            PlannedDeliverables=[],
        )
    )
    existing_remote = repository.create_task(
        NewTaskInput(
            ProjectId=project.Id,
            ParentTaskId=None,
            Name="already-imported",
            Type="Task",
            AssigneeUserId=member.Id,
            CreatorUserId=top.Id,
            StartDate=date(2026, 4, 1),
            EndDate=date(2026, 4, 5),
            AssignedManHour=0,
            PlannedDeliverables=[],
        )
    )
    repository.set_task_iquavis_link(
        existing_remote.Id,
        iquavis_task_id="REMOTE-EXISTING",
        iquavis_project_id="P1",
    )

    workbook_path = tmp_path / "iquavis_editer.xlsm"
    _write_xlsm_fixture(workbook_path, local_parent_id=local_parent.Id)

    sync = IQuavisSyncService(repository=repository, tms_service=service, client=FakeIQuavisClient({}))
    result = sync.import_tasks_from_xlsm(
        workbook_path=workbook_path,
        sheet_names=["551個人業務計画 (1)", "551_全体日程_20240401"],
        creator_user_name="top",
        default_assignee_name="member",
    )

    paired = {task.IQuavisTaskId: task for task in repository.list_paired_tasks()}

    assert result.selected_count == 5
    assert result.duplicate_count == 1
    assert result.skipped_existing_count == 1
    assert len(result.imported_local_ids) == 3
    assert paired["REMOTE-PARENT-XLSM"].ParentTaskId is None
    assert paired["REMOTE-CHILD-XLSM"].ParentTaskId == paired["REMOTE-PARENT-XLSM"].Id
    assert paired["REMOTE-CHILD-XLSM"].Status == "Completion"
    assert paired["REMOTE-LOCAL-CHILD"].ParentTaskId == local_parent.Id
    assert paired["REMOTE-EXISTING"].Id == existing_remote.Id


def test_import_tasks_from_xlsm_uses_creator_when_assignee_is_blank(tmp_path) -> None:
    service, repository, top_id, member_id, _ = _build_service(tmp_path)
    repository.ensure_users(
        names=["top", "member"],
        top_manager_name="top",
        free_time_rate_default=0.2,
        iquavis_user_id_by_name={"member": "REMOTE-CREATOR-USER"},
    )

    workbook_path = tmp_path / "creator_fallback.xlsm"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "tasks"
    sheet.append(
        [
            "Id",
            "Name",
            "Type",
            "StartDate",
            "EndDate",
            "ProjectId",
            "Assigns",
            "CreateUserId",
            "読み取り対象",
            "親タスクId",
            "ParentTaskId",
        ]
    )
    sheet.append(
        [
            "REMOTE-CREATOR-FALLBACK",
            "creator fallback",
            "Task",
            "2026-04-01",
            "2026-04-10",
            "P1",
            "",
            "REMOTE-CREATOR-USER",
            SELECTION_MARK,
            "",
            "",
        ]
    )
    workbook.save(workbook_path)
    workbook.close()

    sync = IQuavisSyncService(repository=repository, tms_service=service, client=FakeIQuavisClient({}))
    imported_ids = sync.import_tasks_from_xlsm(
        workbook_path=workbook_path,
        sheet_names=["tasks"],
        creator_user_name="top",
        default_assignee_name="top",
    ).imported_local_ids

    imported = repository.get_task(imported_ids[0])
    assert imported is not None
    assert top_id != member_id
    assert imported.AssigneeUserId == member_id


def test_import_remote_tasks_resolves_assignee_by_iquavis_user_id(tmp_path) -> None:
    service, repository, _, member_id, _ = _build_service(tmp_path)
    repository.ensure_users(
        names=["top", "member"],
        top_manager_name="top",
        free_time_rate_default=0.2,
        login_id_by_name={"member": "LOCAL-MEMBER"},
        iquavis_user_id_by_name={"member": "REMOTE-USER-1"},
    )
    remote = replace(
        _remote_task(task_id="REMOTE-BY-USER-ID", name="resolved-by-user-id"),
        first_assign_iquavis_user_id="REMOTE-USER-1",
        first_assign_login_id="UNKNOWN-LOGIN",
        first_assign_name="unknown",
    )
    sync = IQuavisSyncService(
        repository=repository,
        tms_service=service,
        client=FakeIQuavisClient({remote.id: remote}),
    )

    local_ids = sync.import_remote_tasks(
        iquavis_task_ids=[remote.id],
        creator_user_name="top",
        default_assignee_name="top",
    )
    imported = repository.get_task(local_ids[0])

    assert imported is not None
    assert imported.AssigneeUserId == member_id
