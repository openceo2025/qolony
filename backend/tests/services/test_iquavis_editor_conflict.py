from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from app.iquavis.editor_conflict import (
    IQUAVIS_DERIVED_CONFLICT_MESSAGE,
    IquavisEditorConflictSyncService,
)
from app.iquavis.editor_models import (
    IquavisEditorQolonyUpdate,
    IquavisEditorSnapshot,
    IquavisEditorSyncWriteResult,
    IquavisTaskDomainRecord,
    IquavisTaskRecord,
    IquavisTaskSyncProperties,
    IquavisTaskSyncRecord,
    SyncedIquavisTarget,
)
from app.iquavis.sync import (
    IquavisConflictPermissionError,
    IquavisConflictStaleError,
    IQuavisSyncService,
)
from app.iquavis.sync_state import (
    IQUAVIS_CONFLICT_STATE,
    IQUAVIS_CONFLICT_HOLD_STATE,
    IQUAVIS_PENDING_SAVE_STATE,
    IQUAVIS_TASK_CONFLICT_STATE_KEY,
    IquavisTaskConflict,
    IquavisTaskConflictField,
    parse_conflict_state,
    serialize_conflict_state,
    task_sync_state,
)
from app.tms_core.config import AppConfig, CalendarConfig, IQuavisConfig, Settings, TeamConfig, UsersConfig
from app.tms_core.db.repository import NewTaskInput, TmsRepository
from app.tms_core.db.session import create_all_tables, create_session_factory, create_sqlite_engine
from app.tms_core.services.core import TmsService, UserInputError


class FakeEditorWorkbookAdapter:
    def __init__(self, snapshot: IquavisEditorSnapshot):
        self.snapshot = snapshot
        self.written_updates: list[IquavisEditorQolonyUpdate] = []
        self.restored_updates: list[IquavisEditorQolonyUpdate] = []
        self.cleared_ids: list[str] = []

    def load_snapshot(self, workbook_path: Path) -> IquavisEditorSnapshot:
        return self.snapshot

    def write_qolony_updates(
        self,
        workbook_path: Path,
        updates: list[IquavisEditorQolonyUpdate],
    ) -> IquavisEditorSyncWriteResult:
        self.written_updates.extend(updates)
        update_by_id = {update.iquavis_id: update for update in updates}
        records: list[IquavisTaskSyncRecord] = []
        for record in self.snapshot.task_sync_records:
            update = update_by_id.get(record.iquavis_id)
            if update is None:
                records.append(record)
                continue
            values = {
                field_name: getattr(update.properties, field_name)
                for field_name in update.field_names
            }
            records.append(replace(record, iquavis_current=replace(record.iquavis_current, **values)))
        self.snapshot = replace(self.snapshot, task_sync_records=records)
        if workbook_path.exists():
            workbook_path.write_bytes(workbook_path.read_bytes() + b"written")
        return IquavisEditorSyncWriteResult(updated_ids=[update.iquavis_id for update in updates])

    def restore_iquavis_values(
        self,
        workbook_path: Path,
        updates: list[IquavisEditorQolonyUpdate],
    ) -> IquavisEditorSyncWriteResult:
        self.restored_updates.extend(updates)
        update_by_id = {update.iquavis_id: update for update in updates}
        records: list[IquavisTaskSyncRecord] = []
        for record in self.snapshot.task_sync_records:
            update = update_by_id.get(record.iquavis_id)
            if update is None:
                records.append(record)
                continue
            values = {
                field_name: getattr(update.properties, field_name)
                for field_name in update.field_names
            }
            records.append(replace(record, iquavis_current=replace(record.iquavis_current, **values)))
        self.snapshot = replace(self.snapshot, task_sync_records=records)
        if workbook_path.exists():
            workbook_path.write_bytes(workbook_path.read_bytes() + b"restored")
        return IquavisEditorSyncWriteResult(updated_ids=[update.iquavis_id for update in updates])

    def clear_force_sync_requests(
        self,
        workbook_path: Path,
        iquavis_ids: list[str],
    ) -> IquavisEditorSyncWriteResult:
        self.cleared_ids.extend(iquavis_ids)
        return IquavisEditorSyncWriteResult(cleared_force_sync_ids=iquavis_ids)


def _build_service(tmp_path):
    today = date.today()
    db_path = tmp_path / "editor_conflict.db"
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
        start_date=today - timedelta(days=30),
        end_date=today + timedelta(days=365),
        crash_zone_pct=20.0,
    )
    top = repository.get_top_manager()
    member = repository.get_user_by_name("member")
    assert top is not None and member is not None
    task = repository.create_task(
        NewTaskInput(
            ProjectId=project.Id,
            ParentTaskId=None,
            Name="Task",
            Type="Task",
            AssigneeUserId=member.Id,
            CreatorUserId=top.Id,
            StartDate=today,
            EndDate=today + timedelta(days=5),
            AssignedManHour=120,
            PlannedDeliverables=[],
            Memo="memo",
        )
    )
    repository.set_task_iquavis_link(task.Id, iquavis_task_id="TASK-1", iquavis_project_id="P1")
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
            editor_workbook_path=tmp_path / "iquavis_editor.xlsm",
            daily_sync_hour=2,
            daily_sync_minute=0,
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
    return service, repository, task.Id


def _props(
    *,
    name: str = "Task",
    start_offset: int = 0,
    end_offset: int = 5,
    assigned: int = 120,
    actual: int = 0,
    status: str = "NotStart",
    progress_rate: int | None = 0,
    memo: str = "memo",
) -> IquavisTaskSyncProperties:
    today = date.today()
    return IquavisTaskSyncProperties(
        name=name,
        start_date=today + timedelta(days=start_offset),
        end_date=today + timedelta(days=end_offset),
        assigned_minutes=assigned,
        actual_minutes=actual,
        status=status,
        progress_rate=progress_rate,
        memo=memo,
    )


def _stored_conflicts(repository) -> dict:
    return parse_conflict_state(repository.get_state(IQUAVIS_TASK_CONFLICT_STATE_KEY))


def test_conflict_sync_prefetches_linked_tasks_without_per_record_repository_lookups(
    tmp_path,
    monkeypatch,
) -> None:
    service, repository, _ = _build_service(tmp_path)
    snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            IquavisTaskSyncRecord(
                iquavis_id="TASK-1",
                kind="task",
                previous=_props(),
                iquavis_current=_props(),
            )
        ]
    )
    lookup_count = 0
    original_lookup = repository.get_task_by_iquavis_task_id

    def counted_lookup(iquavis_task_id: str):
        nonlocal lookup_count
        lookup_count += 1
        return original_lookup(iquavis_task_id)

    monkeypatch.setattr(repository, "get_task_by_iquavis_task_id", counted_lookup)

    result = IquavisEditorConflictSyncService(repository=repository, tms_service=service).sync_snapshot(snapshot)

    assert result.conflict_task_ids == []
    assert result.skipped_missing_local_ids == []
    assert lookup_count == 0


def test_conflict_sync_writes_local_only_changes_to_workbook(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    repository.update_task_name(task_id, "Local Task")
    snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            IquavisTaskSyncRecord(
                iquavis_id="TASK-1",
                kind="task",
                previous=_props(),
                iquavis_current=_props(),
            )
        ]
    )
    adapter = FakeEditorWorkbookAdapter(snapshot)

    result = IquavisEditorConflictSyncService(repository=repository, tms_service=service).sync_snapshot(
        snapshot,
        workbook_path=Path("dummy.xlsm"),
        adapter=adapter,
        now=datetime(2026, 4, 29, 2, 0),
    )

    assert result.local_updates_written == ["TASK-1"]
    assert result.pending_iquavis_save_task_ids == [task_id]
    assert adapter.written_updates[0].properties.name == "Local Task"
    assert adapter.written_updates[0].field_names == ("name",)
    assert service.list_tasks()[0].IQuavisSyncState == IQUAVIS_PENDING_SAVE_STATE
    pending = _stored_conflicts(repository)[task_id]
    assert pending.sync_state == IQUAVIS_PENDING_SAVE_STATE
    assert set(pending.fields) == {"name"}


def test_conflict_sync_applies_remote_only_changes_to_qolony(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            IquavisTaskSyncRecord(
                iquavis_id="TASK-1",
                kind="task",
                previous=_props(),
                iquavis_current=_props(name="Remote Task", end_offset=8),
            )
        ]
    )

    result = IquavisEditorConflictSyncService(repository=repository, tms_service=service).sync_snapshot(snapshot)

    task = repository.get_task(task_id)
    assert result.remote_updates_applied == [task_id]
    assert task is not None
    assert task.Name == "Remote Task"
    assert task.EndDate == date.today() + timedelta(days=8)


def test_conflict_sync_marks_unforced_double_change_as_conflict(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    repository.update_task_name(task_id, "Local Task")
    snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            IquavisTaskSyncRecord(
                iquavis_id="TASK-1",
                kind="task",
                previous=_props(),
                iquavis_current=_props(name="Remote Task"),
            )
        ]
    )

    result = IquavisEditorConflictSyncService(repository=repository, tms_service=service).sync_snapshot(snapshot)

    listed = {task.Id: task for task in service.list_tasks()}
    assert result.conflict_task_ids == [task_id]
    assert listed[task_id].IQuavisSyncState == "Conflict"
    assert listed[task_id].IQuavisConflictMessage
    conflict = _stored_conflicts(repository)[task_id]
    assert set(conflict.fields) == {"name"}
    assert conflict.fields["name"].baseline == "Task"
    assert conflict.fields["name"].qolony == "Local Task"
    assert conflict.fields["name"].excel == "Remote Task"


def test_conflict_sync_honors_force_iquavis_to_qolony_and_clears_request(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    repository.update_task_name(task_id, "Local Task")
    snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            IquavisTaskSyncRecord(
                iquavis_id="TASK-1",
                kind="task",
                previous=_props(),
                iquavis_current=_props(name="Remote Task"),
                force_sync="iquavis_to_qolony",
            )
        ]
    )
    adapter = FakeEditorWorkbookAdapter(snapshot)

    result = IquavisEditorConflictSyncService(repository=repository, tms_service=service).sync_snapshot(
        snapshot,
        workbook_path=Path("dummy.xlsm"),
        adapter=adapter,
    )

    task = repository.get_task(task_id)
    assert result.remote_updates_applied == [task_id]
    assert result.forced_sync_cleared == ["TASK-1"]
    assert adapter.cleared_ids == ["TASK-1"]
    assert task is not None
    assert task.Name == "Remote Task"


def test_force_qolony_direction_overrides_remote_only_change_and_clears_request(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            IquavisTaskSyncRecord(
                "TASK-1",
                "task",
                _props(),
                _props(name="Remote Task"),
                force_sync="qolony_to_iquavis",
            )
        ]
    )
    adapter = FakeEditorWorkbookAdapter(snapshot)

    result = IquavisEditorConflictSyncService(repository=repository, tms_service=service).sync_snapshot(
        snapshot,
        workbook_path=tmp_path / "iquavis_editor.xlsm",
        adapter=adapter,
    )

    task = repository.get_task(task_id)
    assert task is not None and task.Name == "Task"
    assert result.remote_updates_applied == []
    assert adapter.written_updates[-1].field_names == ("name",)
    assert result.forced_sync_cleared == ["TASK-1"]


def test_force_iquavis_direction_overrides_local_only_change_and_clears_request(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    repository.update_task_name(task_id, "Local Task")
    snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            IquavisTaskSyncRecord(
                "TASK-1",
                "task",
                _props(),
                _props(),
                force_sync="iquavis_to_qolony",
            )
        ]
    )
    adapter = FakeEditorWorkbookAdapter(snapshot)

    result = IquavisEditorConflictSyncService(repository=repository, tms_service=service).sync_snapshot(
        snapshot,
        workbook_path=tmp_path / "iquavis_editor.xlsm",
        adapter=adapter,
    )

    task = repository.get_task(task_id)
    assert task is not None and task.Name == "Task"
    assert result.remote_updates_applied == [task_id]
    assert result.local_updates_written == []
    assert result.forced_sync_cleared == ["TASK-1"]


def test_force_request_without_a_difference_is_still_cleared(tmp_path) -> None:
    service, repository, _ = _build_service(tmp_path)
    snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            IquavisTaskSyncRecord(
                "TASK-1",
                "task",
                _props(),
                _props(),
                force_sync="qolony_to_iquavis",
            )
        ]
    )
    adapter = FakeEditorWorkbookAdapter(snapshot)

    result = IquavisEditorConflictSyncService(repository=repository, tms_service=service).sync_snapshot(
        snapshot,
        workbook_path=tmp_path / "iquavis_editor.xlsm",
        adapter=adapter,
    )

    assert result.local_updates_written == []
    assert result.remote_updates_applied == []
    assert result.forced_sync_cleared == ["TASK-1"]


def test_conflict_sync_auto_merges_changes_to_different_fields(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    repository.update_task_name(task_id, "Local Task")
    snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            IquavisTaskSyncRecord(
                iquavis_id="TASK-1",
                kind="task",
                previous=_props(),
                iquavis_current=_props(end_offset=8, assigned=999),
            )
        ]
    )
    adapter = FakeEditorWorkbookAdapter(snapshot)

    result = IquavisEditorConflictSyncService(repository=repository, tms_service=service).sync_snapshot(
        snapshot,
        workbook_path=Path("dummy.xlsm"),
        adapter=adapter,
    )

    task = repository.get_task(task_id)
    assert task is not None
    assert task.Name == "Local Task"
    assert task.EndDate == date.today() + timedelta(days=8)
    assert task.AssignedManHour == 120
    assert result.remote_updates_applied == [task_id]
    assert result.local_updates_written == ["TASK-1"]
    assert result.conflict_task_ids == []
    assert adapter.written_updates[0].field_names == ("name",)
    assert _stored_conflicts(repository)[task_id].sync_state == IQUAVIS_PENDING_SAVE_STATE


def test_conflict_sync_does_not_guess_without_baseline(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            IquavisTaskSyncRecord(
                iquavis_id="TASK-1",
                kind="task",
                previous=None,
                iquavis_current=_props(name="Remote Task"),
            )
        ]
    )
    adapter = FakeEditorWorkbookAdapter(snapshot)

    result = IquavisEditorConflictSyncService(repository=repository, tms_service=service).sync_snapshot(
        snapshot,
        workbook_path=Path("dummy.xlsm"),
        adapter=adapter,
    )

    assert result.conflict_task_ids == [task_id]
    assert result.local_updates_written == []
    assert result.remote_updates_applied == []
    conflict = _stored_conflicts(repository)[task_id]
    assert set(conflict.fields) == {"name"}
    assert conflict.fields["name"].baseline is None


def test_pending_save_clears_only_after_baseline_confirms_vba_save(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    repository.update_task_name(task_id, "Local Task")
    conflict_service = IquavisEditorConflictSyncService(repository=repository, tms_service=service)
    first_snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            IquavisTaskSyncRecord("TASK-1", "task", _props(), _props())
        ]
    )
    adapter = FakeEditorWorkbookAdapter(first_snapshot)
    conflict_service.sync_snapshot(
        first_snapshot,
        workbook_path=Path("dummy.xlsm"),
        adapter=adapter,
    )
    assert _stored_conflicts(repository)[task_id].sync_state == IQUAVIS_PENDING_SAVE_STATE

    waiting_snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            IquavisTaskSyncRecord("TASK-1", "task", _props(), _props(name="Local Task"))
        ]
    )
    # 保存待ちの間は毎回書き直して黄色へ戻す（赤で止まったセルを再送対象へ復帰させるため）。
    # そのためアダプタが要る。
    waiting_adapter = FakeEditorWorkbookAdapter(waiting_snapshot)
    waiting = conflict_service.sync_snapshot(
        waiting_snapshot,
        workbook_path=Path("dummy.xlsm"),
        adapter=waiting_adapter,
    )
    assert waiting.pending_iquavis_save_task_ids == [task_id]
    assert waiting.retried_task_ids == [task_id]
    assert _stored_conflicts(repository)[task_id].sync_state == IQUAVIS_PENDING_SAVE_STATE

    confirmed = _props(name="Local Task")
    confirmed_snapshot = IquavisEditorSnapshot(
        task_sync_records=[IquavisTaskSyncRecord("TASK-1", "task", confirmed, confirmed)]
    )
    conflict_service.sync_snapshot(confirmed_snapshot)
    assert task_id not in _stored_conflicts(repository)
    assert service.list_tasks()[0].IQuavisSyncState == "Linked"


def test_pending_save_reenters_comparison_when_value_changes(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    repository.update_task_name(task_id, "Local Task")
    conflict_service = IquavisEditorConflictSyncService(repository=repository, tms_service=service)
    first_snapshot = IquavisEditorSnapshot(
        task_sync_records=[IquavisTaskSyncRecord("TASK-1", "task", _props(), _props())]
    )
    conflict_service.sync_snapshot(
        first_snapshot,
        workbook_path=Path("dummy.xlsm"),
        adapter=FakeEditorWorkbookAdapter(first_snapshot),
    )

    repository.update_task_name(task_id, "Local Task 2")
    changed_snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            IquavisTaskSyncRecord("TASK-1", "task", _props(), _props(name="Local Task"))
        ]
    )
    result = conflict_service.sync_snapshot(changed_snapshot)

    assert result.conflict_task_ids == [task_id]
    conflict = _stored_conflicts(repository)[task_id]
    assert conflict.sync_state == IQUAVIS_CONFLICT_STATE
    assert conflict.fields["name"].qolony == "Local Task 2"
    assert conflict.fields["name"].excel == "Local Task"


def test_pending_save_does_not_block_other_field_updates(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    repository.update_task_name(task_id, "Local Task")
    snapshot = IquavisEditorSnapshot(
        task_sync_records=[IquavisTaskSyncRecord("TASK-1", "task", _props(), _props())]
    )
    adapter = FakeEditorWorkbookAdapter(snapshot)
    conflict_service = IquavisEditorConflictSyncService(repository=repository, tms_service=service)
    conflict_service.sync_snapshot(
        snapshot,
        workbook_path=tmp_path / "iquavis_editor.xlsm",
        adapter=adapter,
    )
    waiting_record = adapter.snapshot.task_sync_records[0]
    adapter.snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            replace(waiting_record, iquavis_current=replace(waiting_record.iquavis_current, memo="Excel changed memo"))
        ]
    )

    result = conflict_service.sync_snapshot(
        adapter.snapshot,
        workbook_path=tmp_path / "iquavis_editor.xlsm",
        adapter=adapter,
    )

    task = repository.get_task(task_id)
    pending = _stored_conflicts(repository)[task_id]
    assert task is not None and task.Memo == "Excel changed memo"
    assert result.remote_updates_applied == [task_id]
    assert pending.sync_state == IQUAVIS_PENDING_SAVE_STATE
    assert set(pending.fields) == {"name"}


def test_mixed_auto_merge_and_conflict_keeps_pending_fields(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    repository.update_task_name(task_id, "Local Task")
    repository.update_task_memo(task_id, "local memo")
    snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            IquavisTaskSyncRecord(
                "TASK-1",
                "task",
                _props(),
                _props(name="Remote Task", end_offset=8),
            )
        ]
    )
    adapter = FakeEditorWorkbookAdapter(snapshot)

    result = IquavisEditorConflictSyncService(repository=repository, tms_service=service).sync_snapshot(
        snapshot,
        workbook_path=Path("dummy.xlsm"),
        adapter=adapter,
    )

    task = repository.get_task(task_id)
    conflict = _stored_conflicts(repository)[task_id]
    assert task is not None and task.EndDate == date.today() + timedelta(days=8)
    assert result.local_updates_written == ["TASK-1"]
    assert result.remote_updates_applied == [task_id]
    assert set(conflict.fields) == {"name"}
    assert set(conflict.pending_fields) == {"memo"}
    assert adapter.written_updates[0].field_names == ("memo",)


def test_conflict_list_and_qolony_resolution_use_optimistic_lock(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    workbook_path = service.settings.iquavis.editor_workbook_path
    assert workbook_path is not None
    workbook_path.write_bytes(b"workbook")
    repository.update_task_name(task_id, "Local Task")
    snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            IquavisTaskSyncRecord("TASK-1", "task", _props(), _props(name="Remote Task"))
        ]
    )
    adapter = FakeEditorWorkbookAdapter(snapshot)
    fingerprint = hashlib.sha256(workbook_path.read_bytes()).hexdigest()
    IquavisEditorConflictSyncService(repository=repository, tms_service=service).sync_snapshot(
        snapshot,
        workbook_path=workbook_path,
        adapter=adapter,
        now=datetime(2026, 4, 29, 2, 0),
        workbook_fingerprint=fingerprint,
    )
    sync = IQuavisSyncService(repository=repository, tms_service=service)
    listed = sync.list_editor_conflicts()
    assert listed["count"] == 1
    item = listed["items"][0]
    assert item["task_id"] == task_id
    assert item["sync_state"] == IQUAVIS_CONFLICT_STATE
    assert item["fields"] == [
        {"field": "name", "baseline": "Task", "qolony": "Local Task", "excel": "Remote Task"}
    ]

    task = repository.get_task(task_id)
    assert task is not None
    with pytest.raises(IquavisConflictStaleError):
        sync.resolve_editor_conflict(
            task_id=task_id,
            operator_user_id=task.AssigneeUserId,
            direction="qolony_to_iquavis",
            expected_recorded_at="2026-04-29T02:00:01",
            adapter=adapter,
        )

    response = sync.resolve_editor_conflict(
        task_id=task_id,
        operator_user_id=task.AssigneeUserId,
        direction="qolony_to_iquavis",
        expected_recorded_at=item["recorded_at"],
        adapter=adapter,
        now=datetime(2026, 4, 29, 2, 5),
    )
    assert response["sync_state"] == IQUAVIS_PENDING_SAVE_STATE
    pending = _stored_conflicts(repository)[task_id]
    assert pending.workbook_fingerprint == hashlib.sha256(workbook_path.read_bytes()).hexdigest()
    assert adapter.written_updates[-1].field_names == ("name",)


def test_conflict_resolution_accepts_assignee_or_top_manager_and_rejects_other_user(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    repository.ensure_users(
        names=["top", "member", "other"],
        top_manager_name="top",
        free_time_rate_default=0.2,
    )
    workbook_path = service.settings.iquavis.editor_workbook_path
    assert workbook_path is not None
    workbook_path.write_bytes(b"workbook")
    repository.update_task_name(task_id, "Local Task")
    snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            IquavisTaskSyncRecord("TASK-1", "task", _props(), _props(name="Remote Task"))
        ]
    )
    adapter = FakeEditorWorkbookAdapter(snapshot)
    IquavisEditorConflictSyncService(repository=repository, tms_service=service).sync_snapshot(
        snapshot,
        workbook_path=workbook_path,
        adapter=adapter,
    )
    sync = IQuavisSyncService(repository=repository, tms_service=service)
    conflict = _stored_conflicts(repository)[task_id]
    other = repository.get_user_by_name("other")
    top = repository.get_top_manager()
    assert other is not None and top is not None
    with pytest.raises(IquavisConflictPermissionError):
        sync.resolve_editor_conflict(
            task_id=task_id,
            operator_user_id=other.Id,
            direction="iquavis_to_qolony",
            expected_recorded_at=conflict.recorded_at.isoformat(),
            adapter=adapter,
        )

    response = sync.resolve_editor_conflict(
        task_id=task_id,
        operator_user_id=top.Id,
        direction="iquavis_to_qolony",
        expected_recorded_at=conflict.recorded_at.isoformat(),
        adapter=adapter,
    )
    assert response["sync_state"] == "Linked"
    task = repository.get_task(task_id)
    assert task is not None and task.Name == "Remote Task"
    assert task_id not in _stored_conflicts(repository)


def test_legacy_conflict_state_is_readable_but_not_exposed_as_conflict(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    repository.set_state(
        IQUAVIS_TASK_CONFLICT_STATE_KEY,
        json.dumps(
            {
                task_id: {
                    "iquavis_id": "TASK-1",
                    "message": "legacy",
                    "recorded_at": "2026-04-29T02:00:00",
                }
            }
        ),
    )
    parsed = _stored_conflicts(repository)

    assert task_id in parsed
    assert task_sync_state(task_id=task_id, has_iquavis_link=True, conflicts=parsed) == "Linked"
    assert IQuavisSyncService(repository=repository, tms_service=service).list_editor_conflicts() == {
        "items": [],
        "count": 0,
    }


def test_set_editor_conflict_hold_transitions_conflict_state_without_wiping_fields(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    task = repository.get_task(task_id)
    assert task is not None
    editor_path = service.settings.iquavis.editor_workbook_path
    assert editor_path is not None
    editor_path.write_bytes(b"workbook")
    adapter = FakeEditorWorkbookAdapter(IquavisEditorSnapshot())

    repository.set_state(
        IQUAVIS_TASK_CONFLICT_STATE_KEY,
        json.dumps(
            {
                task_id: {
                    "iquavis_id": "TASK-1",
                    "message": "task-level conflict",
                    "recorded_at": "2026-04-29T02:00:00",
                    "fields": {
                        "name": {"baseline": "before", "qolony": "local", "excel": "remote"},
                    },
                    "pending_fields": {},
                    "sync_state": IQUAVIS_CONFLICT_STATE,
                }
            }
        ),
    )

    sync = IQuavisSyncService(repository=repository, tms_service=service)
    held = sync.set_editor_conflict_hold(
        task_id=task_id,
        operator_user_id=task.AssigneeUserId,
        hold=True,
    )
    assert held["sync_state"] == IQUAVIS_CONFLICT_HOLD_STATE
    assert held["task_id"] == task_id

    conflicts = parse_conflict_state(repository.get_state(IQUAVIS_TASK_CONFLICT_STATE_KEY))
    assert conflicts[task_id].sync_state == IQUAVIS_CONFLICT_HOLD_STATE

    unheld = sync.set_editor_conflict_hold(
        task_id=task_id,
        operator_user_id=task.AssigneeUserId,
        hold=False,
        adapter=adapter,
    )
    assert unheld["sync_state"] in {IQUAVIS_CONFLICT_STATE, "Linked"}


def test_set_editor_conflict_hold_rejects_non_conflict_state(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    task = repository.get_task(task_id)
    assert task is not None
    repository.set_state(
        IQUAVIS_TASK_CONFLICT_STATE_KEY,
        json.dumps(
            {
                task_id: {
                    "iquavis_id": "TASK-1",
                    "message": "resolved conflict",
                    "recorded_at": "2026-04-29T02:00:00",
                    "fields": {},
                    "pending_fields": {},
                    "sync_state": "Linked",
                }
            }
        ),
    )

    with pytest.raises(UserInputError):
        IQuavisSyncService(repository=repository, tms_service=service).set_editor_conflict_hold(
            task_id=task_id,
            operator_user_id=task.AssigneeUserId,
            hold=True,
        )


def test_progress_rate_is_qolony_owned_and_written_without_excel_adoption(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    # QOLONYが実績から算出した進捗（60/120=50%）は計測結果なのでExcelへ書き出す。
    repository.update_task_actual_manhour(task_id, 60)
    snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            IquavisTaskSyncRecord(
                "TASK-1",
                "task",
                _props(actual=60, progress_rate=0),
                _props(actual=60, progress_rate=55),
            )
        ]
    )
    adapter = FakeEditorWorkbookAdapter(snapshot)

    result = IquavisEditorConflictSyncService(repository=repository, tms_service=service).sync_snapshot(
        snapshot,
        workbook_path=tmp_path / "iquavis_editor.xlsm",
        adapter=adapter,
    )

    task = repository.get_task(task_id)
    pending = _stored_conflicts(repository)[task_id]
    assert task is not None and task.ProgressRate == 50
    assert result.remote_updates_applied == []
    assert result.local_updates_written == ["TASK-1"]
    assert adapter.written_updates[-1].field_names == ("progress_rate",)
    assert pending.sync_state == IQUAVIS_PENDING_SAVE_STATE
    assert pending.fields["progress_rate"].qolony == 50
    assert pending.fields["progress_rate"].excel == 50


def test_unmeasured_progress_rate_is_not_written_to_excel(tmp_path) -> None:
    # 実績が1分も無く完了・中止でもないタスクの進捗率0は未知でしかない。
    # これをExcelへ書き出すと「サーバーへ保存」でiQUAVISの進捗が0で消える。
    service, repository, task_id = _build_service(tmp_path)
    snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            IquavisTaskSyncRecord(
                "TASK-1",
                "task",
                _props(progress_rate=50),
                _props(progress_rate=50),
            )
        ]
    )
    adapter = FakeEditorWorkbookAdapter(snapshot)

    result = IquavisEditorConflictSyncService(repository=repository, tms_service=service).sync_snapshot(
        snapshot,
        workbook_path=tmp_path / "iquavis_editor.xlsm",
        adapter=adapter,
    )

    task = repository.get_task(task_id)
    assert task is not None and task.ProgressRate == 0
    assert result.local_updates_written == []
    assert result.pending_iquavis_save_task_ids == []
    assert adapter.written_updates == []
    assert _stored_conflicts(repository) == {}


def test_completed_task_progress_is_written_even_without_actual(tmp_path) -> None:
    # 完了は状態そのものが進捗100%を決めるので、実績が無くても計測結果として送る。
    service, repository, task_id = _build_service(tmp_path)
    repository.update_task_status(task_id, "Completion", 100.0)
    snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            IquavisTaskSyncRecord(
                "TASK-1",
                "task",
                _props(status="Completion", progress_rate=40),
                _props(status="Completion", progress_rate=40),
            )
        ]
    )
    adapter = FakeEditorWorkbookAdapter(snapshot)

    IquavisEditorConflictSyncService(repository=repository, tms_service=service).sync_snapshot(
        snapshot,
        workbook_path=tmp_path / "iquavis_editor.xlsm",
        adapter=adapter,
    )

    assert adapter.written_updates[-1].field_names == ("progress_rate",)
    assert _stored_conflicts(repository)[task_id].fields["progress_rate"].qolony == 100


def test_aborted_task_progress_is_not_written_when_nothing_was_measured(tmp_path) -> None:
    # QOLONYの「中止=0%」は仕様上の割り当てで計測値ではない。
    # iQUAVISは中止時点までの進捗（50%）を持っているので、0で上書きしない。
    service, repository, task_id = _build_service(tmp_path)
    repository.update_task_status(task_id, "Abort", 0.0)
    snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            IquavisTaskSyncRecord(
                "TASK-1",
                "task",
                _props(status="Abort", progress_rate=50),
                _props(status="Abort", progress_rate=50),
            )
        ]
    )
    adapter = FakeEditorWorkbookAdapter(snapshot)

    result = IquavisEditorConflictSyncService(repository=repository, tms_service=service).sync_snapshot(
        snapshot,
        workbook_path=tmp_path / "iquavis_editor.xlsm",
        adapter=adapter,
    )

    assert result.local_updates_written == []
    assert adapter.written_updates == []
    assert _stored_conflicts(repository) == {}


def test_existing_unmeasured_progress_pending_is_dropped_on_next_sync(tmp_path) -> None:
    # 旧版が書き出した「基準50 / QOLONY 0 / Excel 0」の保存待ちは、解決手段が無いまま滞留する。
    # 送らない方針にした以上、次の同期で保存待ちからも外す。
    service, repository, task_id = _build_service(tmp_path)
    repository.set_state(
        IQUAVIS_TASK_CONFLICT_STATE_KEY,
        serialize_conflict_state(
            {
                task_id: IquavisTaskConflict(
                    task_id=task_id,
                    iquavis_id="TASK-1",
                    message="QOLONYの再計算値をExcelへ書き出しました。",
                    recorded_at=datetime(2026, 7, 23, 15, 36),
                    fields={
                        "progress_rate": IquavisTaskConflictField(
                            field="progress_rate", baseline=50, qolony=0, excel=0
                        )
                    },
                    sync_state=IQUAVIS_PENDING_SAVE_STATE,
                )
            }
        ),
    )
    snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            IquavisTaskSyncRecord(
                "TASK-1",
                "task",
                _props(progress_rate=50),
                _props(progress_rate=0),
            )
        ]
    )
    adapter = FakeEditorWorkbookAdapter(snapshot)

    IquavisEditorConflictSyncService(repository=repository, tms_service=service).sync_snapshot(
        snapshot,
        workbook_path=tmp_path / "iquavis_editor.xlsm",
        adapter=adapter,
    )

    assert _stored_conflicts(repository) == {}


def test_remote_actual_recomputes_progress_and_writes_final_value_in_same_sync(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            IquavisTaskSyncRecord(
                "TASK-1",
                "task",
                _props(actual=0, progress_rate=0),
                _props(actual=60, progress_rate=0),
            )
        ]
    )
    adapter = FakeEditorWorkbookAdapter(snapshot)

    result = IquavisEditorConflictSyncService(repository=repository, tms_service=service).sync_snapshot(
        snapshot,
        workbook_path=tmp_path / "iquavis_editor.xlsm",
        adapter=adapter,
    )

    task = repository.get_task(task_id)
    pending = _stored_conflicts(repository)[task_id]
    assert task is not None and task.ActualManHour == 60 and task.ProgressRate == pytest.approx(50.0)
    assert result.remote_updates_applied == [task_id]
    assert result.local_updates_written == ["TASK-1"]
    assert adapter.written_updates[-1].field_names == ("progress_rate",)
    assert pending.fields["progress_rate"].qolony == 50
    assert pending.fields["progress_rate"].excel == 50


def test_parent_actual_status_and_progress_use_qolony_aggregate_values(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    parent = repository.get_task(task_id)
    member = repository.get_user_by_name("member")
    top = repository.get_top_manager()
    assert parent is not None and member is not None and top is not None
    child = repository.create_task(
        NewTaskInput(
            ProjectId=parent.ProjectId,
            ParentTaskId=task_id,
            Name="Child",
            Type="Task",
            AssigneeUserId=member.Id,
            CreatorUserId=top.Id,
            StartDate=parent.StartDate,
            EndDate=parent.EndDate,
            AssignedManHour=120,
            PlannedDeliverables=[],
            Memo="",
        )
    )
    repository.update_task_actual_manhour(child.Id, 60)
    repository.update_task_status(child.Id, "DuringStart", 50.0)
    service.recompute_all_task_progress()
    snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            IquavisTaskSyncRecord(
                "TASK-1",
                "task",
                _props(actual=0, status="Abort", progress_rate=0),
                _props(actual=0, status="Abort", progress_rate=0),
            )
        ]
    )
    adapter = FakeEditorWorkbookAdapter(snapshot)

    result = IquavisEditorConflictSyncService(repository=repository, tms_service=service).sync_snapshot(
        snapshot,
        workbook_path=tmp_path / "iquavis_editor.xlsm",
        adapter=adapter,
    )

    pending = _stored_conflicts(repository)[task_id]
    assert result.remote_updates_applied == []
    assert adapter.written_updates[-1].field_names == ("actual_minutes", "status", "progress_rate")
    assert pending.sync_state == IQUAVIS_PENDING_SAVE_STATE
    assert pending.fields["actual_minutes"].qolony == 60
    assert pending.fields["status"].qolony == "DuringStart"
    assert pending.fields["progress_rate"].qolony == 50


def test_new_unknown_excel_status_requires_qolony_direction(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    workbook_path = service.settings.iquavis.editor_workbook_path
    assert workbook_path is not None
    workbook_path.write_bytes(b"workbook")
    snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            IquavisTaskSyncRecord(
                "TASK-1",
                "task",
                _props(status="NotStart"),
                _props(status="UnknownStatus"),
            )
        ]
    )
    adapter = FakeEditorWorkbookAdapter(snapshot)
    IquavisEditorConflictSyncService(repository=repository, tms_service=service).sync_snapshot(
        snapshot,
        workbook_path=workbook_path,
        adapter=adapter,
    )
    conflict = _stored_conflicts(repository)[task_id]
    assert conflict.fields["status"].excel == "UnknownStatus"

    task = repository.get_task(task_id)
    assert task is not None
    with pytest.raises(UserInputError, match="QOLONYで表現できません"):
        IQuavisSyncService(repository=repository, tms_service=service).resolve_editor_conflict(
            task_id=task_id,
            operator_user_id=task.AssigneeUserId,
            direction="iquavis_to_qolony",
            expected_recorded_at=conflict.recorded_at.isoformat(),
            adapter=adapter,
        )


def test_established_unknown_excel_status_is_kept_as_a_known_representation_gap(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            IquavisTaskSyncRecord(
                "TASK-1",
                "task",
                _props(status="UnknownStatus"),
                _props(status="UnknownStatus"),
            )
        ]
    )
    adapter = FakeEditorWorkbookAdapter(snapshot)

    result = IquavisEditorConflictSyncService(repository=repository, tms_service=service).sync_snapshot(
        snapshot,
        workbook_path=tmp_path / "iquavis_editor.xlsm",
        adapter=adapter,
    )

    assert result.conflict_task_ids == []
    assert result.local_updates_written == []
    assert task_id not in _stored_conflicts(repository)


def test_established_unknown_status_conflicts_after_qolony_work_starts(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    repository.update_task_status(task_id, "DuringStart", 0.0)
    snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            IquavisTaskSyncRecord(
                "TASK-1",
                "task",
                _props(status="UnknownStatus"),
                _props(status="UnknownStatus"),
            )
        ]
    )

    result = IquavisEditorConflictSyncService(repository=repository, tms_service=service).sync_snapshot(snapshot)

    conflict = _stored_conflicts(repository)[task_id]
    assert result.conflict_task_ids == [task_id]
    assert conflict.fields["status"].qolony == "DuringStart"
    assert conflict.fields["status"].excel == "UnknownStatus"


def test_abort_is_applied_as_a_supported_remote_status(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            IquavisTaskSyncRecord(
                "TASK-1",
                "task",
                _props(status="NotStart"),
                _props(status="Abort"),
            )
        ]
    )

    result = IquavisEditorConflictSyncService(repository=repository, tms_service=service).sync_snapshot(snapshot)

    task = repository.get_task(task_id)
    assert task is not None and task.Status == "Abort"
    assert result.remote_updates_applied == [task_id]
    assert result.conflict_task_ids == []


def test_abort_is_terminal_and_excluded_from_parent_progress(tmp_path) -> None:
    service, repository, parent_id = _build_service(tmp_path)
    parent = repository.get_task(parent_id)
    member = repository.get_user_by_name("member")
    top = repository.get_top_manager()
    assert parent is not None and member is not None and top is not None
    completed = repository.create_task(
        NewTaskInput(
            ProjectId=parent.ProjectId,
            ParentTaskId=parent_id,
            Name="Completed",
            Type="Task",
            AssigneeUserId=member.Id,
            CreatorUserId=top.Id,
            StartDate=date.today(),
            EndDate=date.today(),
            AssignedManHour=120,
            PlannedDeliverables=[],
            Memo="",
        )
    )
    aborted = repository.create_task(
        NewTaskInput(
            ProjectId=parent.ProjectId,
            ParentTaskId=parent_id,
            Name="Aborted",
            Type="Task",
            AssigneeUserId=member.Id,
            CreatorUserId=top.Id,
            StartDate=date.today(),
            EndDate=date.today(),
            AssignedManHour=600,
            PlannedDeliverables=[],
            Memo="",
        )
    )
    repository.update_task_actual_manhour(aborted.Id, 300)
    repository.update_task_status(completed.Id, "Completion", 100.0)
    repository.update_task_status(aborted.Id, "Abort", 0.0)

    service.recompute_all_task_progress()

    refreshed_parent = repository.get_task(parent_id)
    refreshed_abort = repository.get_task(aborted.Id)
    assert refreshed_parent is not None
    assert refreshed_parent.Status == "Completion"
    assert refreshed_parent.ProgressRate == 100.0
    assert refreshed_abort is not None
    assert refreshed_abort.ActualManHour == 300
    assert refreshed_abort.ProgressRate == 0.0
    assert aborted.Id not in {task.Id for task in service.list_my_tasks(member.Id)}
    assert aborted.Id not in {task.Id for task in service.list_today_tasks(member.Id)}
    with pytest.raises(UserInputError, match="中止"):
        service.start_timer(
            member.Id,
            aborted.Id,
            is_ojt=False,
            teacher_user_id=None,
        )
    with pytest.raises(UserInputError, match="中止"):
        service.set_task_memo(aborted.Id, "must not change", operator_user_id=member.Id)


def test_parent_becomes_abort_when_all_children_are_aborted(tmp_path) -> None:
    service, repository, parent_id = _build_service(tmp_path)
    parent = repository.get_task(parent_id)
    member = repository.get_user_by_name("member")
    top = repository.get_top_manager()
    assert parent is not None and member is not None and top is not None
    for name in ("Abort A", "Abort B"):
        child = repository.create_task(
            NewTaskInput(
                ProjectId=parent.ProjectId,
                ParentTaskId=parent_id,
                Name=name,
                Type="Task",
                AssigneeUserId=member.Id,
                CreatorUserId=top.Id,
                StartDate=date.today(),
                EndDate=date.today(),
                AssignedManHour=120,
                PlannedDeliverables=[],
                Memo="",
            )
        )
        repository.update_task_status(child.Id, "Abort", 0.0)

    service.recompute_all_task_progress()

    refreshed_parent = repository.get_task(parent_id)
    assert refreshed_parent is not None
    assert refreshed_parent.Status == "Abort"
    assert refreshed_parent.ProgressRate == 0.0


def test_abort_is_terminal_until_reopened(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    member = repository.get_user_by_name("member")
    project = service.get_project()
    assert member is not None
    repository.update_task_status(task_id, "Abort", 0.0)

    with pytest.raises(UserInputError, match="中止"):
        service.update_task_schedule(
            task_id,
            date.today(),
            date.today() + timedelta(days=10),
            operator_user_id=member.Id,
        )
    with pytest.raises(UserInputError, match="中止"):
        service.update_task_child_dependency_mode(
            task_id=task_id,
            child_dependency_mode="Parallel",
            operator_user_id=member.Id,
        )
    with pytest.raises(UserInputError, match="再開してから"):
        service.complete_task(task_id, operator_user_id=member.Id)
    assert task_id not in {task.Id for task in repository.list_incomplete_tasks(project.Id)}

    service.reopen_task(task_id, operator_user_id=member.Id)

    reopened = repository.get_task(task_id)
    assert reopened is not None and reopened.Status == "DuringStart"
    assert task_id in {task.Id for task in repository.list_incomplete_tasks(project.Id)}


def test_excel_resolution_writes_recomputed_progress_and_stays_pending(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    workbook_path = service.settings.iquavis.editor_workbook_path
    assert workbook_path is not None
    workbook_path.write_bytes(b"workbook")
    repository.update_task_actual_manhour(task_id, 60)
    service.recompute_all_task_progress()
    snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            IquavisTaskSyncRecord(
                "TASK-1",
                "task",
                _props(actual=0, progress_rate=0),
                _props(actual=30, progress_rate=25),
            )
        ]
    )
    adapter = FakeEditorWorkbookAdapter(snapshot)
    IquavisEditorConflictSyncService(repository=repository, tms_service=service).sync_snapshot(
        snapshot,
        workbook_path=workbook_path,
        adapter=adapter,
    )
    conflict = _stored_conflicts(repository)[task_id]

    task = repository.get_task(task_id)
    assert task is not None
    response = IQuavisSyncService(repository=repository, tms_service=service).resolve_editor_conflict(
        task_id=task_id,
        operator_user_id=task.AssigneeUserId,
        direction="iquavis_to_qolony",
        expected_recorded_at=conflict.recorded_at.isoformat(),
        adapter=adapter,
    )

    task = repository.get_task(task_id)
    pending = _stored_conflicts(repository)[task_id]
    assert task is not None and task.ActualManHour == 30 and task.ProgressRate == pytest.approx(25.0)
    assert response["sync_state"] == IQUAVIS_PENDING_SAVE_STATE
    assert pending.fields["progress_rate"].qolony == 25
    assert pending.fields["progress_rate"].excel == 25
    assert adapter.written_updates[-1].field_names == ("progress_rate",)


def test_resolving_one_conflict_keeps_other_conflict_and_refreshes_its_fingerprint(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    first = repository.get_task(task_id)
    member = repository.get_user_by_name("member")
    top = repository.get_top_manager()
    assert first is not None and member is not None and top is not None
    second = repository.create_task(
        NewTaskInput(
            ProjectId=first.ProjectId,
            ParentTaskId=None,
            Name="Second Local",
            Type="Task",
            AssigneeUserId=member.Id,
            CreatorUserId=top.Id,
            StartDate=first.StartDate,
            EndDate=first.EndDate,
            AssignedManHour=120,
            PlannedDeliverables=[],
            Memo="memo",
        )
    )
    repository.set_task_iquavis_link(second.Id, iquavis_task_id="TASK-2", iquavis_project_id="P1")
    repository.update_task_name(task_id, "First Local")
    workbook_path = service.settings.iquavis.editor_workbook_path
    assert workbook_path is not None
    workbook_path.write_bytes(b"workbook")
    snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            IquavisTaskSyncRecord("TASK-1", "task", _props(name="First"), _props(name="First Excel")),
            IquavisTaskSyncRecord("TASK-2", "task", _props(name="Second"), _props(name="Second Excel")),
        ]
    )
    adapter = FakeEditorWorkbookAdapter(snapshot)
    IquavisEditorConflictSyncService(repository=repository, tms_service=service).sync_snapshot(
        snapshot,
        workbook_path=workbook_path,
        adapter=adapter,
    )
    first_conflict = _stored_conflicts(repository)[task_id]

    response = IQuavisSyncService(repository=repository, tms_service=service).resolve_editor_conflict(
        task_id=task_id,
        operator_user_id=member.Id,
        direction="qolony_to_iquavis",
        expected_recorded_at=first_conflict.recorded_at.isoformat(),
        adapter=adapter,
    )

    remaining = _stored_conflicts(repository)
    expected_fingerprint = hashlib.sha256(workbook_path.read_bytes()).hexdigest()
    assert response["sync_state"] == IQUAVIS_PENDING_SAVE_STATE
    assert remaining[second.Id].sync_state == IQUAVIS_CONFLICT_STATE
    assert remaining[second.Id].workbook_fingerprint == expected_fingerprint
    assert adapter.written_updates[-1].iquavis_id == "TASK-1"


def test_excel_resolution_does_not_apply_unrelated_workbook_changes(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    first = repository.get_task(task_id)
    member = repository.get_user_by_name("member")
    top = repository.get_top_manager()
    assert first is not None and member is not None and top is not None
    second = repository.create_task(
        NewTaskInput(
            ProjectId=first.ProjectId,
            ParentTaskId=None,
            Name="Second",
            Type="Task",
            AssigneeUserId=member.Id,
            CreatorUserId=top.Id,
            StartDate=first.StartDate,
            EndDate=first.EndDate,
            AssignedManHour=120,
            PlannedDeliverables=[],
            Memo="memo",
        )
    )
    repository.set_task_iquavis_link(second.Id, iquavis_task_id="TASK-2", iquavis_project_id="P1")
    repository.update_task_name(task_id, "First Local")
    workbook_path = service.settings.iquavis.editor_workbook_path
    assert workbook_path is not None
    workbook_path.write_bytes(b"workbook")
    initial_snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            IquavisTaskSyncRecord("TASK-1", "task", _props(name="First"), _props(name="First Excel"))
        ]
    )
    adapter = FakeEditorWorkbookAdapter(initial_snapshot)
    IquavisEditorConflictSyncService(repository=repository, tms_service=service).sync_snapshot(
        initial_snapshot,
        workbook_path=workbook_path,
        adapter=adapter,
    )
    conflict = _stored_conflicts(repository)[task_id]
    adapter.snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            *initial_snapshot.task_sync_records,
            IquavisTaskSyncRecord("TASK-2", "task", _props(name="Second"), _props(name="Second Excel")),
        ]
    )

    IQuavisSyncService(repository=repository, tms_service=service).resolve_editor_conflict(
        task_id=task_id,
        operator_user_id=member.Id,
        direction="iquavis_to_qolony",
        expected_recorded_at=conflict.recorded_at.isoformat(),
        adapter=adapter,
    )

    unchanged = repository.get_task(second.Id)
    assert unchanged is not None and unchanged.Name == "Second"


def test_manual_excel_refresh_detects_conflicts_immediately(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    workbook_path = service.settings.iquavis.editor_workbook_path
    member = repository.get_user_by_name("member")
    assert workbook_path is not None and member is not None
    workbook_path.write_bytes(b"workbook")
    repository.update_task_name(task_id, "Local Task")
    adapter = FakeEditorWorkbookAdapter(
        IquavisEditorSnapshot(
            task_sync_records=[
                IquavisTaskSyncRecord("TASK-1", "task", _props(), _props(name="Remote Task"))
            ]
        )
    )

    response = IQuavisSyncService(repository=repository, tms_service=service).refresh_editor_conflicts(
        operator_user_id=member.Id,
        adapter=adapter,
    )

    assert response["conflict_count"] == 1
    assert response["pending_count"] == 0
    assert _stored_conflicts(repository)[task_id].sync_state == IQUAVIS_CONFLICT_STATE


def test_authoritative_import_preview_warns_and_uses_hidden_snapshot_for_excel_differences(tmp_path) -> None:
    service, repository, _ = _build_service(tmp_path)
    workbook_path = service.settings.iquavis.editor_workbook_path
    top = repository.get_top_manager()
    assert workbook_path is not None and top is not None
    workbook_path.write_bytes(b"workbook")
    adapter = FakeEditorWorkbookAdapter(
        IquavisEditorSnapshot(
            task_sync_records=[
                IquavisTaskSyncRecord(
                    "TASK-1",
                    "task",
                    _props(status="Completion", progress_rate=100),
                    _props(status="NotStart", progress_rate=0),
                )
            ]
        )
    )

    preview = IQuavisSyncService(repository=repository, tms_service=service).preview_editor_authoritative_import(
        operator_user_id=top.Id,
        adapter=adapter,
    )

    assert preview["ready"] is True
    assert preview["dirty_excel_task_count"] == 1
    assert preview["excel_snapshot_difference_count"] == 1
    assert preview["excel_snapshot_difference_details"] == [
        {
            "iquavis_id": "TASK-1",
            "task_name": "Task",
            "fields": ["status", "progress_rate"],
        }
    ]
    assert preview["blockers"] == []
    assert any("非表示シートの取得成功値" in warning for warning in preview["warnings"])
    assert adapter.written_updates == []


def test_authoritative_import_preview_normalizes_excel_time_serial_text(tmp_path) -> None:
    service, repository, _ = _build_service(tmp_path)
    workbook_path = service.settings.iquavis.editor_workbook_path
    top = repository.get_top_manager()
    assert workbook_path is not None and top is not None
    workbook_path.write_bytes(b"workbook")
    adapter = FakeEditorWorkbookAdapter(
        IquavisEditorSnapshot(
            task_sync_records=[
                IquavisTaskSyncRecord(
                    "TASK-1",
                    "task",
                    _props(name="16:00:00"),
                    _props(name=str(2 / 3)),
                )
            ]
        )
    )

    preview = IQuavisSyncService(repository=repository, tms_service=service).preview_editor_authoritative_import(
        operator_user_id=top.Id,
        adapter=adapter,
    )

    assert preview["ready"] is True
    assert preview["excel_snapshot_difference_count"] == 1
    assert preview["semantic_excel_snapshot_difference_count"] == 0


def test_authoritative_import_preview_ignores_blank_iquavis_id_records(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    workbook_path = service.settings.iquavis.editor_workbook_path
    top = repository.get_top_manager()
    assert workbook_path is not None and top is not None
    workbook_path.write_bytes(b"workbook")
    remote = _props(name="Task from iQUAVIS", status="Completion", progress_rate=100)
    local = _props(name="Task", status="NotStart", progress_rate=0)
    snapshot = IquavisEditorSnapshot(
        tasks=[
            IquavisTaskRecord(
                iquavis_task_id="TASK-1",
                name=local.name,
                project_id="P1",
                task_domain_id=None,
                parent_task_id=None,
                start_date=local.start_date,
                end_date=local.end_date,
                task_type="Task",
                status=local.status,
                progress_rate=local.progress_rate,
            )
        ],
        task_sync_records=[
            IquavisTaskSyncRecord("", "task", _props(name="blank row"), _props(name="blank row")),
            IquavisTaskSyncRecord("TASK-1", "task", remote, local),
        ],
    )
    adapter = FakeEditorWorkbookAdapter(snapshot)
    sync = IQuavisSyncService(repository=repository, tms_service=service)
    preview = sync.preview_editor_authoritative_import(operator_user_id=top.Id, adapter=adapter)

    assert preview["ready"] is True
    assert preview["scanned_task_count"] == 1
    assert preview["dirty_excel_task_count"] == 1
    assert any("iQUAVIS IDが空の同期行が1件あるため" in warning for warning in preview["warnings"])

    result = sync.apply_editor_authoritative_import(
        operator_user_id=top.Id,
        expected_workbook_fingerprint=preview["workbook_fingerprint"],
        confirmation="IQUAVIS_TO_QOLONY",
        adapter=adapter,
    )

    updated = repository.get_task(task_id)
    assert updated is not None
    assert updated.Name == "Task from iQUAVIS"
    assert updated.Status == "Completion"
    assert result["status"] == "ok"
    assert result["updated_task_count"] == 1
    assert adapter.restored_updates and all(update.iquavis_id.strip() for update in adapter.restored_updates)


def test_authoritative_import_preview_all_records_without_iquavis_id_are_ignored(tmp_path) -> None:
    service, repository, _ = _build_service(tmp_path)
    workbook_path = service.settings.iquavis.editor_workbook_path
    top = repository.get_top_manager()
    assert workbook_path is not None and top is not None
    workbook_path.write_bytes(b"workbook")
    adapter = FakeEditorWorkbookAdapter(
        IquavisEditorSnapshot(
            task_sync_records=[
                IquavisTaskSyncRecord("", "task", _props(name="vacation 1"), _props(name="vacation 1")),
                IquavisTaskSyncRecord(" ", "task", _props(name="vacation 2"), _props(name="vacation 2")),
                IquavisTaskSyncRecord("0", "task", _props(name="vacation 3"), _props(name="vacation 3")),
            ]
        )
    )

    preview = IQuavisSyncService(repository=repository, tms_service=service).preview_editor_authoritative_import(
        operator_user_id=top.Id,
        adapter=adapter,
    )

    assert preview["ready"] is True
    assert preview["scanned_task_count"] == 0
    assert preview["ignored_blank_record_count"] == 3
    assert preview["blockers"] == []
    assert any("iQUAVIS IDが空の同期行が3件あるため" in warning for warning in preview["warnings"])


def test_authoritative_import_preview_blocks_missing_hidden_baseline(tmp_path) -> None:
    service, repository, _ = _build_service(tmp_path)
    workbook_path = service.settings.iquavis.editor_workbook_path
    top = repository.get_top_manager()
    assert workbook_path is not None and top is not None
    workbook_path.write_bytes(b"workbook")
    adapter = FakeEditorWorkbookAdapter(
        IquavisEditorSnapshot(
            task_sync_records=[IquavisTaskSyncRecord("TASK-1", "task", None, _props())]
        )
    )

    preview = IQuavisSyncService(repository=repository, tms_service=service).preview_editor_authoritative_import(
        operator_user_id=top.Id,
        adapter=adapter,
    )

    assert preview["ready"] is False
    assert preview["missing_baseline_task_count"] == 1
    assert preview["missing_baseline_details"][0]["iquavis_id"] == "TASK-1"
    assert any("取得/保存成功値がない" in blocker for blocker in preview["blockers"])


def test_authoritative_import_preview_can_ignore_missing_baseline_by_review_item(tmp_path) -> None:
    service, repository, _ = _build_service(tmp_path)
    workbook_path = service.settings.iquavis.editor_workbook_path
    top = repository.get_top_manager()
    assert workbook_path is not None and top is not None
    workbook_path.write_bytes(b"workbook")

    remote = _props(name="Missing baseline")
    adapter = FakeEditorWorkbookAdapter(
        IquavisEditorSnapshot(
            tasks=[
                IquavisTaskRecord(
                    iquavis_task_id="TASK-MISSING-BL",
                    name=remote.name,
                    project_id="P1",
                    task_domain_id=None,
                    parent_task_id=None,
                    start_date=remote.start_date,
                    end_date=remote.end_date,
                    task_type="Task",
                )
            ],
            task_sync_records=[
                IquavisTaskSyncRecord(
                    "TASK-MISSING-BL",
                    "task",
                    None,
                    remote,
                )
            ],
        )
    )
    sync = IQuavisSyncService(repository=repository, tms_service=service)

    blocked_preview = sync.preview_editor_authoritative_import(operator_user_id=top.Id, adapter=adapter)
    assert blocked_preview["ready"] is False
    assert blocked_preview["missing_baseline_task_count"] == 1
    assert blocked_preview["review_items"][0]["iquavis_id"] == "TASK-MISSING-BL"
    assert blocked_preview["review_items"][0]["kind"] == "missing_baseline"

    preview = sync.preview_editor_authoritative_import(
        operator_user_id=top.Id,
        adapter=adapter,
        ignored_iquavis_ids=["TASK-MISSING-BL"],
    )
    assert preview["ready"] is True
    assert preview["review_items"] == []

    result = sync.apply_editor_authoritative_import(
        operator_user_id=top.Id,
        expected_workbook_fingerprint=preview["workbook_fingerprint"],
        confirmation="IQUAVIS_TO_QOLONY",
        adapter=adapter,
        ignored_iquavis_ids=["TASK-MISSING-BL"],
    )
    assert result["status"] == "ok"
    assert result["imported_task_count"] == 0

    ignored_by_key = sync.apply_editor_authoritative_import(
        operator_user_id=top.Id,
        expected_workbook_fingerprint=preview["workbook_fingerprint"],
        confirmation="IQUAVIS_TO_QOLONY",
        adapter=adapter,
        ignored_review_item_keys=["row:0"],
    )
    assert ignored_by_key["status"] == "ok"
    assert ignored_by_key["imported_task_count"] == 0


def test_authoritative_import_preview_can_ignore_missing_baseline_by_review_item_key(tmp_path) -> None:
    service, repository, _ = _build_service(tmp_path)
    workbook_path = service.settings.iquavis.editor_workbook_path
    top = repository.get_top_manager()
    assert workbook_path is not None and top is not None
    workbook_path.write_bytes(b"workbook")

    remote = _props(name="Missing baseline")
    adapter = FakeEditorWorkbookAdapter(
        IquavisEditorSnapshot(
            tasks=[
                IquavisTaskRecord(
                    iquavis_task_id="TASK-MISSING-BL",
                    name=remote.name,
                    project_id="P1",
                    task_domain_id=None,
                    parent_task_id=None,
                    start_date=remote.start_date,
                    end_date=remote.end_date,
                    task_type="Task",
                )
            ],
            task_sync_records=[
                IquavisTaskSyncRecord(
                    "TASK-MISSING-BL",
                    "task",
                    None,
                    remote,
                )
            ],
        )
    )
    sync = IQuavisSyncService(repository=repository, tms_service=service)

    blocked_preview = sync.preview_editor_authoritative_import(operator_user_id=top.Id, adapter=adapter)
    assert blocked_preview["ready"] is False
    assert blocked_preview["review_items"][0]["review_item_key"] == "row:0"

    preview = sync.preview_editor_authoritative_import(
        operator_user_id=top.Id,
        adapter=adapter,
        ignored_review_item_keys=["row:0"],
    )
    assert preview["ready"] is True
    assert preview["review_items"] == []


def test_authoritative_import_preview_can_ignore_blank_id_by_review_item_key(tmp_path) -> None:
    service, repository, _ = _build_service(tmp_path)
    workbook_path = service.settings.iquavis.editor_workbook_path
    top = repository.get_top_manager()
    assert workbook_path is not None and top is not None
    workbook_path.write_bytes(b"workbook")

    adapter = FakeEditorWorkbookAdapter(
        IquavisEditorSnapshot(
            task_sync_records=[IquavisTaskSyncRecord("", "task", _props(), _props(name="vacation"))]
        )
    )

    sync = IQuavisSyncService(repository=repository, tms_service=service)
    preview = sync.preview_editor_authoritative_import(operator_user_id=top.Id, adapter=adapter)
    assert preview["ready"] is True
    assert preview["ignored_blank_record_count"] == 1
    assert preview["scanned_task_count"] == 0
    assert preview["warnings"][0].startswith("iQUAVIS IDが空の同期行が1件あるため")

    with_review_item_keys = sync.preview_editor_authoritative_import(
        operator_user_id=top.Id,
        adapter=adapter,
        ignored_review_item_keys=["row:0"],
    )
    assert with_review_item_keys["ready"] is True
    assert with_review_item_keys["scanned_task_count"] == 0


def test_authoritative_import_preview_accepts_abort_and_blocks_unknown_status(tmp_path) -> None:
    service, repository, _ = _build_service(tmp_path)
    workbook_path = service.settings.iquavis.editor_workbook_path
    top = repository.get_top_manager()
    assert workbook_path is not None and top is not None
    workbook_path.write_bytes(b"workbook")
    adapter = FakeEditorWorkbookAdapter(
        IquavisEditorSnapshot(
            task_sync_records=[
                IquavisTaskSyncRecord("TASK-ABORT", "task", _props(status="Abort"), _props(status="Abort")),
                IquavisTaskSyncRecord(
                    "TASK-UNKNOWN",
                    "task",
                    _props(status="UnknownStatus"),
                    _props(status="UnknownStatus"),
                ),
            ]
        )
    )

    preview = IQuavisSyncService(repository=repository, tms_service=service).preview_editor_authoritative_import(
        operator_user_id=top.Id,
        adapter=adapter,
    )

    assert preview["ready"] is False
    assert preview["status_breakdown"] == {"Abort": 1, "UnknownStatus": 1}
    assert preview["unknown_status_count"] == 1
    assert preview["unsupported_status_count"] == 1
    assert preview["unknown_status_details"][0]["iquavis_id"] == "TASK-UNKNOWN"


def test_authoritative_import_preview_rejects_workbook_changed_during_read(tmp_path) -> None:
    service, repository, _ = _build_service(tmp_path)
    workbook_path = service.settings.iquavis.editor_workbook_path
    top = repository.get_top_manager()
    assert workbook_path is not None and top is not None
    workbook_path.write_bytes(b"workbook")

    class MutatingLoadAdapter(FakeEditorWorkbookAdapter):
        def load_snapshot(self, path: Path) -> IquavisEditorSnapshot:
            path.write_bytes(path.read_bytes() + b"changed")
            return self.snapshot

    adapter = MutatingLoadAdapter(IquavisEditorSnapshot())

    with pytest.raises(IquavisConflictStaleError, match="読み取り中"):
        IQuavisSyncService(repository=repository, tms_service=service).preview_editor_authoritative_import(
            operator_user_id=top.Id,
            adapter=adapter,
        )


def test_authoritative_import_preview_blocks_unresolved_remote_parent_before_mutation(tmp_path) -> None:
    service, repository, _ = _build_service(tmp_path)
    workbook_path = service.settings.iquavis.editor_workbook_path
    top = repository.get_top_manager()
    assert workbook_path is not None and top is not None
    workbook_path.write_bytes(b"workbook")
    server = _props(name="Remote child")
    adapter = FakeEditorWorkbookAdapter(
        IquavisEditorSnapshot(
            synced_targets=[SyncedIquavisTarget(iquavis_id="TASK-CHILD", kind="task")],
            tasks=[
                IquavisTaskRecord(
                    iquavis_task_id="TASK-CHILD",
                    name=server.name,
                    project_id="P1",
                    task_domain_id=None,
                    parent_task_id="TASK-MISSING-PARENT",
                    start_date=server.start_date,
                    end_date=server.end_date,
                )
            ],
            task_sync_records=[
                IquavisTaskSyncRecord("TASK-CHILD", "task", server, server)
            ],
        )
    )

    preview = IQuavisSyncService(
        repository=repository,
        tms_service=service,
    ).preview_editor_authoritative_import(operator_user_id=top.Id, adapter=adapter)

    assert preview["ready"] is False
    assert preview["structure_error_count"] == 1
    assert preview["structure_error_details"][0]["iquavis_id"] == "TASK-CHILD"
    assert "親タスク" in preview["structure_error_details"][0]["reason"]
    assert list(tmp_path.glob("editor_conflict.pre_iquavis_reconcile_*.db")) == []


def test_authoritative_import_preview_blocks_duplicate_or_reassigned_explicit_local_link(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    workbook_path = service.settings.iquavis.editor_workbook_path
    top = repository.get_top_manager()
    assert workbook_path is not None and top is not None
    workbook_path.write_bytes(b"workbook")
    first = _props(name="First")
    second = _props(name="Second")
    adapter = FakeEditorWorkbookAdapter(
        IquavisEditorSnapshot(
            synced_targets=[
                SyncedIquavisTarget(
                    iquavis_id="TASK-1",
                    kind="task",
                    qolony_task_id=task_id,
                ),
                SyncedIquavisTarget(
                    iquavis_id="TASK-2",
                    kind="task",
                    qolony_task_id=task_id,
                ),
            ],
            tasks=[
                IquavisTaskRecord(
                    iquavis_task_id="TASK-1",
                    name=first.name,
                    project_id="P1",
                    task_domain_id=None,
                    parent_task_id=None,
                    start_date=first.start_date,
                    end_date=first.end_date,
                ),
                IquavisTaskRecord(
                    iquavis_task_id="TASK-2",
                    name=second.name,
                    project_id="P1",
                    task_domain_id=None,
                    parent_task_id=None,
                    start_date=second.start_date,
                    end_date=second.end_date,
                ),
            ],
            task_sync_records=[
                IquavisTaskSyncRecord("TASK-1", "task", first, first),
                IquavisTaskSyncRecord("TASK-2", "task", second, second),
            ],
        )
    )

    preview = IQuavisSyncService(
        repository=repository,
        tms_service=service,
    ).preview_editor_authoritative_import(operator_user_id=top.Id, adapter=adapter)

    reasons = [item["reason"] for item in preview["structure_error_details"]]
    assert preview["ready"] is False
    assert any("複数のiQUAVIS ID" in reason for reason in reasons)
    assert any("別のiQUAVIS IDへリンク済み" in reason for reason in reasons)
    linked = repository.get_task(task_id)
    assert linked is not None and linked.IQuavisTaskId == "TASK-1"
    assert list(tmp_path.glob("editor_conflict.pre_iquavis_reconcile_*.db")) == []


def test_authoritative_import_preview_and_apply_can_ignore_structure_error_tasks(tmp_path) -> None:
    service, repository, _ = _build_service(tmp_path)
    workbook_path = service.settings.iquavis.editor_workbook_path
    top = repository.get_top_manager()
    assert workbook_path is not None and top is not None
    workbook_path.write_bytes(b"workbook")

    parent_server = _props(name="RemoteParent")
    child_server = _props(name="RemoteChild")
    missing_parent_id = "TASK-MISSING-PARENT"
    adapter = FakeEditorWorkbookAdapter(
        IquavisEditorSnapshot(
            synced_targets=[
                SyncedIquavisTarget(iquavis_id="TASK-PARENT", kind="task"),
                SyncedIquavisTarget(iquavis_id="TASK-CHILD", kind="task"),
            ],
            tasks=[
                IquavisTaskRecord(
                    iquavis_task_id="TASK-PARENT",
                    name=parent_server.name,
                    project_id="P1",
                    task_domain_id=None,
                    parent_task_id=None,
                    start_date=parent_server.start_date,
                    end_date=parent_server.end_date,
                    task_type="Task",
                ),
                IquavisTaskRecord(
                    iquavis_task_id="TASK-CHILD",
                    name=child_server.name,
                    project_id="P1",
                    task_domain_id=None,
                    parent_task_id=missing_parent_id,
                    start_date=child_server.start_date,
                    end_date=child_server.end_date,
                    task_type="Task",
                ),
            ],
            task_sync_records=[
                IquavisTaskSyncRecord("TASK-PARENT", "task", parent_server, parent_server),
                IquavisTaskSyncRecord("TASK-CHILD", "task", child_server, child_server),
            ],
        )
    )
    sync = IQuavisSyncService(repository=repository, tms_service=service)

    blocked_preview = sync.preview_editor_authoritative_import(operator_user_id=top.Id, adapter=adapter)
    assert blocked_preview["ready"] is False
    assert blocked_preview["structure_error_count"] == 1
    assert blocked_preview["structure_error_details"][0]["iquavis_id"] == "TASK-CHILD"
    assert blocked_preview["review_items"][0]["iquavis_id"] == "TASK-CHILD"
    assert blocked_preview["review_items"][0]["kind"] == "structure:task"
    assert blocked_preview["review_items"][0]["reason"] == blocked_preview["structure_error_details"][0]["reason"]

    preview = sync.preview_editor_authoritative_import(
        operator_user_id=top.Id,
        adapter=adapter,
        ignored_iquavis_ids=["TASK-CHILD"],
    )

    assert preview["ready"] is True
    assert preview["review_items"] == []
    assert preview["structure_error_count"] == 0
    assert preview["new_task_count"] == 1

    result = sync.apply_editor_authoritative_import(
        operator_user_id=top.Id,
        expected_workbook_fingerprint=preview["workbook_fingerprint"],
        confirmation="IQUAVIS_TO_QOLONY",
        adapter=adapter,
        ignored_iquavis_ids=["TASK-CHILD"],
    )

    assert result["status"] == "ok"
    assert result["imported_task_count"] == 1
    local_tasks = repository.list_tasks(service.get_project().Id)
    assert any(task.Name == "RemoteParent" for task in local_tasks)


def test_authoritative_import_requires_force_sync_clear_before_database_mutation(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    workbook_path = service.settings.iquavis.editor_workbook_path
    top = repository.get_top_manager()
    assert workbook_path is not None and top is not None
    workbook_path.write_bytes(b"workbook")
    server = _props(name="Server Task")

    class RefusingClearAdapter(FakeEditorWorkbookAdapter):
        def clear_force_sync_requests(
            self,
            path: Path,
            iquavis_ids: list[str],
        ) -> IquavisEditorSyncWriteResult:
            return IquavisEditorSyncWriteResult()

    adapter = RefusingClearAdapter(
        IquavisEditorSnapshot(
            synced_targets=[
                SyncedIquavisTarget(
                    iquavis_id="TASK-1",
                    kind="task",
                    force_sync="iquavis_to_qolony",
                )
            ],
            tasks=[
                IquavisTaskRecord(
                    iquavis_task_id="TASK-1",
                    name=server.name,
                    project_id="P1",
                    task_domain_id=None,
                    parent_task_id=None,
                    start_date=server.start_date,
                    end_date=server.end_date,
                )
            ],
            task_sync_records=[
                IquavisTaskSyncRecord(
                    "TASK-1",
                    "task",
                    server,
                    server,
                    force_sync="iquavis_to_qolony",
                )
            ],
        )
    )
    sync = IQuavisSyncService(repository=repository, tms_service=service)
    preview = sync.preview_editor_authoritative_import(operator_user_id=top.Id, adapter=adapter)

    with pytest.raises(UserInputError, match="強制同期指定をクリア"):
        sync.apply_editor_authoritative_import(
            operator_user_id=top.Id,
            expected_workbook_fingerprint=preview["workbook_fingerprint"],
            confirmation="IQUAVIS_TO_QOLONY",
            adapter=adapter,
        )

    task = repository.get_task(task_id)
    assert task is not None and task.Name == "Task"
    assert len(list(tmp_path.glob("editor_conflict.pre_iquavis_reconcile_*.db"))) == 1


def test_authoritative_import_applies_clean_server_snapshot_without_excel_writeback(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    workbook_path = service.settings.iquavis.editor_workbook_path
    top = repository.get_top_manager()
    assert workbook_path is not None and top is not None
    workbook_path.write_bytes(b"clean-workbook")
    server = _props(
        name="Server Task",
        end_offset=9,
        actual=120,
        status="Completion",
        progress_rate=100,
        memo="server memo",
    )
    snapshot = IquavisEditorSnapshot(
        tasks=[
            IquavisTaskRecord(
                iquavis_task_id="TASK-1",
                name=server.name,
                project_id="P1",
                task_domain_id=None,
                parent_task_id=None,
                start_date=server.start_date,
                end_date=server.end_date,
                status=server.status,
                progress_rate=server.progress_rate,
                note=server.memo,
            )
        ],
        task_sync_records=[
            IquavisTaskSyncRecord("TASK-1", "task", server, server)
        ],
    )
    adapter = FakeEditorWorkbookAdapter(snapshot)
    sync = IQuavisSyncService(repository=repository, tms_service=service)
    preview = sync.preview_editor_authoritative_import(operator_user_id=top.Id, adapter=adapter)
    repository.set_state(
        IQUAVIS_TASK_CONFLICT_STATE_KEY,
        serialize_conflict_state(
            {
                task_id: IquavisTaskConflict(
                    task_id=task_id,
                    iquavis_id="TASK-1",
                    message="pending",
                    recorded_at=datetime(2026, 7, 20, 12, 0),
                    fields={
                        "progress_rate": IquavisTaskConflictField(
                            field="progress_rate",
                            baseline=100,
                            qolony=0,
                            excel=0,
                        )
                    },
                    sync_state=IQUAVIS_PENDING_SAVE_STATE,
                )
            }
        ),
    )

    result = sync.apply_editor_authoritative_import(
        operator_user_id=top.Id,
        expected_workbook_fingerprint=preview["workbook_fingerprint"],
        confirmation="IQUAVIS_TO_QOLONY",
        adapter=adapter,
        now=datetime(2026, 7, 20, 12, 30),
    )

    task = repository.get_task(task_id)
    assert preview["ready"] is True
    assert task is not None
    assert task.Name == "Server Task"
    assert task.EndDate == date.today() + timedelta(days=9)
    assert task.ActualManHour == 120
    assert task.Status == "Completion"
    assert task.ProgressRate == 100
    assert task.Memo == "server memo"
    assert result["updated_task_count"] == 1
    assert result["cleared_state_count"] == 1
    assert result["derived_progress_mismatch_count"] == 0
    backup_path = Path(result["backup_path"])
    assert backup_path.exists()
    assert backup_path.parent == tmp_path
    assert backup_path.name.startswith("editor_conflict.pre_iquavis_reconcile_")
    with sqlite3.connect(backup_path) as backup_connection:
        backed_up_name = backup_connection.execute(
            'SELECT "Name" FROM "Tasks" WHERE "Id" = ?',
            (task_id,),
        ).fetchone()
    assert backed_up_name == ("Task",)
    assert _stored_conflicts(repository) == {}
    assert adapter.written_updates == []


def test_authoritative_parent_rollup_difference_stays_conflict_until_qolony_is_adopted(tmp_path) -> None:
    service, repository, parent_id = _build_service(tmp_path)
    workbook_path = service.settings.iquavis.editor_workbook_path
    top = repository.get_top_manager()
    member = repository.get_user_by_name("member")
    project = service.get_project()
    assert workbook_path is not None and top is not None and member is not None
    workbook_path.write_bytes(b"workbook")
    repository.update_task_type(parent_id, "GroupTask")
    child = repository.create_task(
        NewTaskInput(
            ProjectId=project.Id,
            ParentTaskId=parent_id,
            Name="Child",
            Type="Task",
            AssigneeUserId=member.Id,
            CreatorUserId=top.Id,
            StartDate=date.today(),
            EndDate=date.today() + timedelta(days=5),
            AssignedManHour=120,
            PlannedDeliverables=[],
            Memo="",
        )
    )
    repository.set_task_iquavis_link(
        child.Id,
        iquavis_task_id="TASK-CHILD",
        iquavis_project_id="P1",
    )
    parent_server = _props(
        name="Parent",
        actual=120,
        status="Completion",
        progress_rate=100,
    )
    child_server = _props(name="Child")
    adapter = FakeEditorWorkbookAdapter(
        IquavisEditorSnapshot(
            synced_targets=[
                SyncedIquavisTarget(iquavis_id="TASK-1", kind="task"),
                SyncedIquavisTarget(iquavis_id="TASK-CHILD", kind="task"),
            ],
            tasks=[
                IquavisTaskRecord(
                    iquavis_task_id="TASK-1",
                    name="Parent",
                    project_id="P1",
                    task_domain_id=None,
                    parent_task_id=None,
                    start_date=parent_server.start_date,
                    end_date=parent_server.end_date,
                    task_type="GroupTask",
                ),
                IquavisTaskRecord(
                    iquavis_task_id="TASK-CHILD",
                    name="Child",
                    project_id="P1",
                    task_domain_id=None,
                    parent_task_id="TASK-1",
                    start_date=child_server.start_date,
                    end_date=child_server.end_date,
                ),
            ],
            task_sync_records=[
                IquavisTaskSyncRecord("TASK-1", "task", parent_server, parent_server),
                IquavisTaskSyncRecord("TASK-CHILD", "task", child_server, child_server),
            ],
        )
    )
    sync = IQuavisSyncService(repository=repository, tms_service=service)
    preview = sync.preview_editor_authoritative_import(operator_user_id=top.Id, adapter=adapter)

    result = sync.apply_editor_authoritative_import(
        operator_user_id=top.Id,
        expected_workbook_fingerprint=preview["workbook_fingerprint"],
        confirmation="IQUAVIS_TO_QOLONY",
        adapter=adapter,
        now=datetime(2026, 7, 20, 13, 0),
    )

    parent = repository.get_task(parent_id)
    conflict = _stored_conflicts(repository)[parent_id]
    assert parent is not None
    assert parent.Status == "DuringStart"
    assert parent.ProgressRate == 0
    assert result["protected_derived_conflict_count"] == 1
    assert conflict.message == IQUAVIS_DERIVED_CONFLICT_MESSAGE
    # 進捗率は親の実績が0で未計測なので、競合項目にも書き出し対象にもしない。
    assert set(conflict.fields) == {"actual_minutes", "status"}
    assert adapter.written_updates == []

    follow_up = sync.run_editor_workbook_sync(adapter=adapter)
    conflict = _stored_conflicts(repository)[parent_id]
    assert follow_up.conflicts.local_updates_written == []
    assert follow_up.conflicts.pending_iquavis_save_task_ids == []
    assert parent_id in follow_up.conflicts.conflict_task_ids
    assert conflict.message == IQUAVIS_DERIVED_CONFLICT_MESSAGE

    with pytest.raises(UserInputError, match="直接上書きできません"):
        sync.resolve_editor_conflict(
            task_id=parent_id,
            operator_user_id=top.Id,
            direction="iquavis_to_qolony",
            expected_recorded_at=conflict.recorded_at.isoformat(),
            adapter=adapter,
        )

    resolved = sync.resolve_editor_conflict(
        task_id=parent_id,
        operator_user_id=top.Id,
        direction="qolony_to_iquavis",
        expected_recorded_at=conflict.recorded_at.isoformat(),
        adapter=adapter,
        now=datetime(2026, 7, 20, 13, 5),
    )

    assert resolved["sync_state"] == IQUAVIS_PENDING_SAVE_STATE
    pending = _stored_conflicts(repository)[parent_id]
    assert pending.sync_state == IQUAVIS_PENDING_SAVE_STATE
    assert set(adapter.written_updates[-1].field_names) == {
        "actual_minutes",
        "status",
    }


def test_authoritative_import_discards_visible_draft_and_applies_hidden_server_values(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    workbook_path = service.settings.iquavis.editor_workbook_path
    top = repository.get_top_manager()
    assert workbook_path is not None and top is not None
    workbook_path.write_bytes(b"draft-workbook")
    server = _props(
        name="16:00:00",
        end_offset=8,
        actual=90,
        status="Completion",
        progress_rate=100,
        memo="server memo",
    )
    visible_draft = _props(
        name=str(2 / 3),
        end_offset=2,
        actual=0,
        status="NotStart",
        progress_rate=0,
        memo="yellow draft",
    )
    snapshot = IquavisEditorSnapshot(
        tasks=[
            IquavisTaskRecord(
                iquavis_task_id="TASK-1",
                name=visible_draft.name,
                project_id="P1",
                task_domain_id=None,
                parent_task_id=None,
                start_date=visible_draft.start_date,
                end_date=visible_draft.end_date,
                status=visible_draft.status,
                progress_rate=visible_draft.progress_rate,
                note=visible_draft.memo,
            )
        ],
        task_sync_records=[IquavisTaskSyncRecord("TASK-1", "task", server, visible_draft)],
    )
    adapter = FakeEditorWorkbookAdapter(snapshot)
    sync = IQuavisSyncService(repository=repository, tms_service=service)
    preview = sync.preview_editor_authoritative_import(operator_user_id=top.Id, adapter=adapter)

    result = sync.apply_editor_authoritative_import(
        operator_user_id=top.Id,
        expected_workbook_fingerprint=preview["workbook_fingerprint"],
        confirmation="IQUAVIS_TO_QOLONY",
        adapter=adapter,
    )

    task = repository.get_task(task_id)
    assert preview["ready"] is True
    assert preview["excel_snapshot_difference_count"] == 1
    assert task is not None
    assert task.Name == "16:00:00"
    assert task.EndDate == date.today() + timedelta(days=8)
    assert task.ActualManHour == 90
    assert task.Status == "Completion"
    assert task.Memo == "server memo"
    assert Path(result["backup_path"]).exists()
    assert result["restored_excel_task_count"] == 1
    assert [update.iquavis_id for update in adapter.restored_updates] == ["TASK-1"]

    follow_up = sync.run_editor_workbook_sync(adapter=adapter)
    after_follow_up = repository.get_task(task_id)
    assert after_follow_up is not None and after_follow_up.Name == "16:00:00"
    assert after_follow_up.Status == "Completion"
    assert follow_up.conflicts.remote_updates_applied == []
    assert follow_up.conflicts.local_updates_written == []
    assert follow_up.conflicts.conflict_task_ids == []


def test_authoritative_import_uses_actual_bound_database_when_config_path_is_relative(tmp_path) -> None:
    service, repository, _ = _build_service(tmp_path)
    service.settings = replace(
        service.settings,
        app=replace(service.settings.app, db_path=Path("data/not-the-bound-database.db")),
    )
    workbook_path = service.settings.iquavis.editor_workbook_path
    top = repository.get_top_manager()
    assert workbook_path is not None and top is not None
    workbook_path.write_bytes(b"workbook")
    server = _props()
    adapter = FakeEditorWorkbookAdapter(
        IquavisEditorSnapshot(
            tasks=[
                IquavisTaskRecord(
                    iquavis_task_id="TASK-1",
                    name=server.name,
                    project_id="P1",
                    task_domain_id=None,
                    parent_task_id=None,
                    start_date=server.start_date,
                    end_date=server.end_date,
                    status=server.status,
                    progress_rate=server.progress_rate,
                    note=server.memo,
                )
            ],
            task_sync_records=[IquavisTaskSyncRecord("TASK-1", "task", server, server)],
        )
    )
    sync = IQuavisSyncService(repository=repository, tms_service=service)
    preview = sync.preview_editor_authoritative_import(operator_user_id=top.Id, adapter=adapter)

    result = sync.apply_editor_authoritative_import(
        operator_user_id=top.Id,
        expected_workbook_fingerprint=preview["workbook_fingerprint"],
        confirmation="IQUAVIS_TO_QOLONY",
        adapter=adapter,
    )

    assert Path(result["backup_path"]).parent == tmp_path


def test_authoritative_import_adds_remote_only_and_preserves_local_only_tasks(tmp_path) -> None:
    service, repository, _ = _build_service(tmp_path)
    workbook_path = service.settings.iquavis.editor_workbook_path
    top = repository.get_top_manager()
    member = repository.get_user_by_name("member")
    project = service.get_project()
    assert workbook_path is not None and top is not None and member is not None
    workbook_path.write_bytes(b"workbook")
    local_only = repository.create_task(
        NewTaskInput(
            ProjectId=project.Id,
            ParentTaskId=None,
            Name="Local Only",
            Type="Task",
            AssigneeUserId=member.Id,
            CreatorUserId=top.Id,
            StartDate=date.today(),
            EndDate=date.today() + timedelta(days=5),
            AssignedManHour=240,
            PlannedDeliverables=[],
            Memo="keep local",
        )
    )
    server = _props(
        name="Remote Only",
        assigned=300,
        actual=45,
        status="Abort",
        progress_rate=0,
        memo="server task",
    )
    snapshot = IquavisEditorSnapshot(
        synced_targets=[SyncedIquavisTarget(iquavis_id="TASK-REMOTE", kind="task")],
        tasks=[
            IquavisTaskRecord(
                iquavis_task_id="TASK-REMOTE",
                name="visible draft name",
                project_id="P1",
                task_domain_id=None,
                parent_task_id=None,
                start_date=server.start_date,
                end_date=server.end_date,
                assigned_minutes=999,
                status="NotStart",
                progress_rate=10,
                note="visible draft",
            )
        ],
        task_sync_records=[
            IquavisTaskSyncRecord(
                "TASK-REMOTE",
                "task",
                server,
                _props(name="visible draft name", assigned=999, status="NotStart", progress_rate=10),
            )
        ],
    )
    adapter = FakeEditorWorkbookAdapter(snapshot)
    sync = IQuavisSyncService(repository=repository, tms_service=service)
    preview = sync.preview_editor_authoritative_import(operator_user_id=top.Id, adapter=adapter)

    result = sync.apply_editor_authoritative_import(
        operator_user_id=top.Id,
        expected_workbook_fingerprint=preview["workbook_fingerprint"],
        confirmation="IQUAVIS_TO_QOLONY",
        adapter=adapter,
    )

    imported = repository.get_task_by_iquavis_task_id("TASK-REMOTE")
    preserved = repository.get_task(local_only.Id)
    assert preview["new_task_count"] == 1
    assert preview["local_only_task_count"] == 1
    assert result["imported_task_count"] == 1
    assert imported is not None
    assert imported.Name == "Remote Only"
    assert imported.AssignedManHour == 0
    assert imported.ActualManHour == 45
    assert imported.Status == "Abort"
    assert imported.Memo == "server task"
    assert preserved is not None
    assert preserved.Name == "Local Only"
    assert preserved.AssignedManHour == 240
    assert preserved.Memo == "keep local"


def test_authoritative_import_preserves_existing_qolony_parent(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    workbook_path = service.settings.iquavis.editor_workbook_path
    top = repository.get_top_manager()
    member = repository.get_user_by_name("member")
    project = service.get_project()
    assert workbook_path is not None and top is not None and member is not None
    workbook_path.write_bytes(b"workbook")

    local_parent = repository.create_task(
        NewTaskInput(
            ProjectId=project.Id,
            ParentTaskId=None,
            Name="QOLONY Parent",
            Type="GroupTask",
            AssigneeUserId=member.Id,
            CreatorUserId=top.Id,
            StartDate=date.today(),
            EndDate=date.today() + timedelta(days=10),
            AssignedManHour=0,
            PlannedDeliverables=[],
            Memo="",
        )
    )
    remote_parent = repository.create_task(
        NewTaskInput(
            ProjectId=project.Id,
            ParentTaskId=None,
            Name="iQUAVIS Parent",
            Type="GroupTask",
            AssigneeUserId=member.Id,
            CreatorUserId=top.Id,
            StartDate=date.today(),
            EndDate=date.today() + timedelta(days=10),
            AssignedManHour=0,
            PlannedDeliverables=[],
            Memo="",
        )
    )
    repository.set_task_iquavis_link(
        remote_parent.Id,
        iquavis_task_id="TASK-REMOTE-PARENT",
        iquavis_project_id="P1",
    )
    repository.update_task_parent(task_id, local_parent.Id)
    child_server = _props(name="Child")
    parent_server = _props(name="iQUAVIS Parent")
    snapshot = IquavisEditorSnapshot(
        synced_targets=[
            SyncedIquavisTarget(iquavis_id="TASK-1", kind="task"),
            SyncedIquavisTarget(iquavis_id="TASK-REMOTE-PARENT", kind="task"),
        ],
        tasks=[
            IquavisTaskRecord(
                iquavis_task_id="TASK-REMOTE-PARENT",
                name=parent_server.name,
                project_id="P1",
                task_domain_id=None,
                parent_task_id=None,
                start_date=parent_server.start_date,
                end_date=parent_server.end_date,
            ),
            IquavisTaskRecord(
                iquavis_task_id="TASK-1",
                name=child_server.name,
                project_id="P1",
                task_domain_id=None,
                parent_task_id="TASK-REMOTE-PARENT",
                start_date=child_server.start_date,
                end_date=child_server.end_date,
            ),
        ],
        task_sync_records=[
            IquavisTaskSyncRecord("TASK-REMOTE-PARENT", "task", parent_server, parent_server),
            IquavisTaskSyncRecord("TASK-1", "task", child_server, child_server),
        ],
    )
    adapter = FakeEditorWorkbookAdapter(snapshot)
    sync = IQuavisSyncService(repository=repository, tms_service=service)
    preview = sync.preview_editor_authoritative_import(operator_user_id=top.Id, adapter=adapter)

    sync.apply_editor_authoritative_import(
        operator_user_id=top.Id,
        expected_workbook_fingerprint=preview["workbook_fingerprint"],
        confirmation="IQUAVIS_TO_QOLONY",
        adapter=adapter,
    )

    refreshed = repository.get_task(task_id)
    assert preview["preserved_local_parent_count"] == 1
    assert refreshed is not None and refreshed.ParentTaskId == local_parent.Id


def test_authoritative_import_applies_explicit_qolony_parent_override(tmp_path) -> None:
    service, repository, task_id = _build_service(tmp_path)
    workbook_path = service.settings.iquavis.editor_workbook_path
    top = repository.get_top_manager()
    member = repository.get_user_by_name("member")
    project = service.get_project()
    assert workbook_path is not None and top is not None and member is not None
    workbook_path.write_bytes(b"workbook")
    explicit_parent = repository.create_task(
        NewTaskInput(
            ProjectId=project.Id,
            ParentTaskId=None,
            Name="Explicit QOLONY Parent",
            Type="GroupTask",
            AssigneeUserId=member.Id,
            CreatorUserId=top.Id,
            StartDate=date.today(),
            EndDate=date.today() + timedelta(days=10),
            AssignedManHour=0,
            PlannedDeliverables=[],
            Memo="",
        )
    )
    server = _props()
    snapshot = IquavisEditorSnapshot(
        synced_targets=[
            SyncedIquavisTarget(
                iquavis_id="TASK-1",
                kind="task",
                qolony_parent_task_id=explicit_parent.Id,
            )
        ],
        tasks=[
            IquavisTaskRecord(
                iquavis_task_id="TASK-1",
                name=server.name,
                project_id="P1",
                task_domain_id=None,
                parent_task_id=None,
                start_date=server.start_date,
                end_date=server.end_date,
            )
        ],
        task_sync_records=[IquavisTaskSyncRecord("TASK-1", "task", server, server)],
    )
    adapter = FakeEditorWorkbookAdapter(snapshot)
    sync = IQuavisSyncService(repository=repository, tms_service=service)
    preview = sync.preview_editor_authoritative_import(operator_user_id=top.Id, adapter=adapter)

    sync.apply_editor_authoritative_import(
        operator_user_id=top.Id,
        expected_workbook_fingerprint=preview["workbook_fingerprint"],
        confirmation="IQUAVIS_TO_QOLONY",
        adapter=adapter,
    )

    refreshed = repository.get_task(task_id)
    assert refreshed is not None and refreshed.ParentTaskId == explicit_parent.Id


def test_authoritative_import_preserves_existing_domain_schedule(tmp_path) -> None:
    service, repository, _ = _build_service(tmp_path)
    workbook_path = service.settings.iquavis.editor_workbook_path
    top = repository.get_top_manager()
    member = repository.get_user_by_name("member")
    project = service.get_project()
    assert workbook_path is not None and top is not None and member is not None
    workbook_path.write_bytes(b"clean-workbook")
    domain_start = date.today() - timedelta(days=10)
    domain_end = date.today() + timedelta(days=20)
    domain = repository.create_task(
        NewTaskInput(
            ProjectId=project.Id,
            ParentTaskId=None,
            Name="Existing Domain",
            Type="GroupTask",
            AssigneeUserId=member.Id,
            CreatorUserId=top.Id,
            StartDate=domain_start,
            EndDate=domain_end,
            AssignedManHour=0,
            PlannedDeliverables=[],
            Memo="",
        )
    )
    repository.set_task_iquavis_link(
        domain.Id,
        iquavis_task_id="DOMAIN-1",
        iquavis_project_id="P1",
    )
    server = _props(status="Completion", progress_rate=100)
    snapshot = IquavisEditorSnapshot(
        synced_targets=[SyncedIquavisTarget(iquavis_id="DOMAIN-1", kind="task_domain")],
        task_domains=[
            IquavisTaskDomainRecord(
                iquavis_task_domain_id="DOMAIN-1",
                name="Server Domain",
                project_id="P1",
                parent_task_domain_id=None,
                # 実ブックreaderと同様、ドメインの日程は読込日の仮値。
                start_date=date.today(),
                end_date=date.today(),
                note="server domain",
            )
        ],
        tasks=[
            IquavisTaskRecord(
                iquavis_task_id="TASK-1",
                name=server.name,
                project_id="P1",
                task_domain_id=None,
                parent_task_id=None,
                start_date=server.start_date,
                end_date=server.end_date,
                status=server.status,
                progress_rate=server.progress_rate,
                note=server.memo,
            )
        ],
        task_sync_records=[IquavisTaskSyncRecord("TASK-1", "task", server, server)],
    )
    adapter = FakeEditorWorkbookAdapter(snapshot)
    sync = IQuavisSyncService(repository=repository, tms_service=service)
    preview = sync.preview_editor_authoritative_import(operator_user_id=top.Id, adapter=adapter)

    sync.apply_editor_authoritative_import(
        operator_user_id=top.Id,
        expected_workbook_fingerprint=preview["workbook_fingerprint"],
        confirmation="IQUAVIS_TO_QOLONY",
        adapter=adapter,
    )

    refreshed = repository.get_task(domain.Id)
    assert refreshed is not None
    assert refreshed.Name == "Server Domain"
    assert refreshed.StartDate == domain_start
    assert refreshed.EndDate == domain_end


def test_authoritative_import_is_top_manager_only(tmp_path) -> None:
    service, repository, _ = _build_service(tmp_path)
    workbook_path = service.settings.iquavis.editor_workbook_path
    member = repository.get_user_by_name("member")
    assert workbook_path is not None and member is not None
    workbook_path.write_bytes(b"workbook")

    with pytest.raises(IquavisConflictPermissionError, match="Top Manager"):
        IQuavisSyncService(repository=repository, tms_service=service).preview_editor_authoritative_import(
            operator_user_id=member.Id,
            adapter=FakeEditorWorkbookAdapter(IquavisEditorSnapshot()),
        )


def test_daily_editor_sync_runs_once_after_configured_time(tmp_path) -> None:
    service, repository, _ = _build_service(tmp_path)
    snapshot = IquavisEditorSnapshot(task_sync_records=[])
    adapter = FakeEditorWorkbookAdapter(snapshot)
    sync = IQuavisSyncService(repository=repository, tms_service=service)
    now = datetime(2026, 4, 29, 2, 0)

    first = sync.run_daily_editor_sync_if_due(now=now, adapter=adapter)
    second = sync.run_daily_editor_sync_if_due(now=now + timedelta(minutes=1), adapter=adapter)

    assert first is True
    assert second is False


def test_daily_editor_sync_runs_excel_server_save_when_enabled(tmp_path, monkeypatch) -> None:
    service, repository, _ = _build_service(tmp_path)
    service.settings = replace(
        service.settings,
        iquavis=replace(
            service.settings.iquavis,
            excel_server_save_enabled=True,
            excel_server_save_expected_project_count=3,
            excel_server_save_timeout_seconds=120,
        ),
    )
    snapshot = IquavisEditorSnapshot(task_sync_records=[])
    adapter = FakeEditorWorkbookAdapter(snapshot)
    workbook_path = service.settings.iquavis.editor_workbook_path
    assert workbook_path is not None
    workbook_path.write_bytes(b"workbook")
    calls: list[dict] = []

    def fake_excel_server_save(**kwargs):
        calls.append(kwargs)
        return {
            "status": "ok",
            "project_count": 3,
            "sent_count": 12,
            "success_count": 12,
            "failure_count": 0,
        }

    monkeypatch.setattr("app.iquavis.sync.run_excel_server_save", fake_excel_server_save)
    sync = IQuavisSyncService(repository=repository, tms_service=service)

    assert sync.run_daily_editor_sync_if_due(
        now=datetime(2026, 4, 29, 2, 0),
        adapter=adapter,
    ) is True
    assert len(calls) == 1
    assert calls[0]["expected_project_count"] == 3
    assert calls[0]["timeout_seconds"] == 120
    event = json.loads(repository.get_state("IQuavisLatestSyncEvent") or "{}")
    assert event["is_error"] is False
    assert "サーバー保存" in event["message"]
    # 案件数だけでなく、実際に何件送れたのかを出す。
    assert "送信12件" in event["message"]
    assert "成功12件" in event["message"]


def test_daily_editor_sync_warns_when_nothing_was_sent_to_iquavis(tmp_path, monkeypatch) -> None:
    # 「3案件 完了」としか出さなかったため、1件も送っていないのに保存できたと読めた。
    service, repository, _ = _build_service(tmp_path)
    service.settings = replace(
        service.settings,
        iquavis=replace(
            service.settings.iquavis,
            excel_server_save_enabled=True,
            excel_server_save_expected_project_count=3,
            excel_server_save_timeout_seconds=120,
        ),
    )
    adapter = FakeEditorWorkbookAdapter(IquavisEditorSnapshot(task_sync_records=[]))
    workbook_path = service.settings.iquavis.editor_workbook_path
    assert workbook_path is not None
    workbook_path.write_bytes(b"workbook")

    monkeypatch.setattr(
        "app.iquavis.sync.run_excel_server_save",
        lambda **_kwargs: {
            "status": "ok",
            "project_count": 3,
            "sent_count": 0,
            "success_count": 0,
            "failure_count": 0,
        },
    )
    sync = IQuavisSyncService(repository=repository, tms_service=service)

    assert sync.run_daily_editor_sync_if_due(
        now=datetime(2026, 4, 29, 2, 0),
        adapter=adapter,
    ) is True

    event = json.loads(repository.get_state("IQuavisLatestSyncEvent") or "{}")
    assert event["is_error"] is True
    assert "送信0件" in event["message"]
    assert "送った変更はありません" in event["message"]


def test_non_working_day_dates_are_not_written_to_excel(tmp_path) -> None:
    # iQUAVISは非稼働日の予定日を保存時にCED0129で拒否する。黄色で書いても必ず失敗するので送らない。
    service, repository, task_id = _build_service(tmp_path)
    sunday = date(2026, 5, 10)
    assert sunday.weekday() == 6
    repository.update_task_schedule(task_id, sunday - timedelta(days=12), sunday)
    snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            IquavisTaskSyncRecord("TASK-1", "task", _props(), _props())
        ]
    )
    adapter = FakeEditorWorkbookAdapter(snapshot)

    result = IquavisEditorConflictSyncService(repository=repository, tms_service=service).sync_snapshot(
        snapshot,
        workbook_path=tmp_path / "iquavis_editor.xlsm",
        adapter=adapter,
    )

    written_fields = [name for update in adapter.written_updates for name in update.field_names]
    assert "end_date" not in written_fields
    assert task_id in result.non_working_date_task_ids


def test_business_day_dates_are_still_written_to_excel(tmp_path) -> None:
    # 稼働日なら従来どおり送る。ガードが広すぎないことを確かめる。
    service, repository, task_id = _build_service(tmp_path)
    friday = date(2026, 5, 8)
    assert friday.weekday() == 4
    repository.update_task_schedule(task_id, friday - timedelta(days=7), friday)
    snapshot = IquavisEditorSnapshot(
        task_sync_records=[
            IquavisTaskSyncRecord("TASK-1", "task", _props(), _props())
        ]
    )
    adapter = FakeEditorWorkbookAdapter(snapshot)

    result = IquavisEditorConflictSyncService(repository=repository, tms_service=service).sync_snapshot(
        snapshot,
        workbook_path=tmp_path / "iquavis_editor.xlsm",
        adapter=adapter,
    )

    written_fields = [name for update in adapter.written_updates for name in update.field_names]
    assert "end_date" in written_fields
    assert result.non_working_date_task_ids == []


def test_schedule_edit_warns_about_non_working_days(tmp_path) -> None:
    # 作った時点で気づけるようにする。Excel保存まで分からないと原因追跡が難しい。
    service, repository, task_id = _build_service(tmp_path)
    saturday = date(2026, 5, 9)
    assert saturday.weekday() == 5

    warnings = service.update_task_schedule(task_id, saturday - timedelta(days=5), saturday)

    assert any("非稼働日" in warning for warning in warnings)


def test_pending_save_is_rewritten_so_a_failed_send_gets_retried(tmp_path) -> None:
    # Excelの「サーバーへ保存」が失敗するとセルは赤になる。VBAは黄色しか送らないため、
    # 書き直さないと二度と再送されず、iQUAVIS側が永久に更新されない。
    service, repository, task_id = _build_service(tmp_path)
    repository.update_task_status(task_id, "Completion", 100.0)
    completed = _props(status="Completion", progress_rate=100)

    # 前回QOLONYが書き出したが、iQUAVISへは保存できていない状態。
    # 表示シートはQOLONY値、非表示シート(取得成功値)は未着手のまま。
    repository.set_state(
        IQUAVIS_TASK_CONFLICT_STATE_KEY,
        serialize_conflict_state(
            {
                task_id: IquavisTaskConflict(
                    task_id=task_id,
                    iquavis_id="TASK-1",
                    message="QOLONYの変更をExcelへ書き出しました。",
                    recorded_at=datetime.now(),
                    fields={
                        "status": IquavisTaskConflictField(
                            field="status", baseline="NotStart", qolony="Completion", excel="Completion"
                        )
                    },
                    pending_fields={},
                    workbook_fingerprint=None,
                    sync_state=IQUAVIS_PENDING_SAVE_STATE,
                )
            }
        ),
    )
    snapshot = IquavisEditorSnapshot(
        task_sync_records=[IquavisTaskSyncRecord("TASK-1", "task", _props(), completed)]
    )
    adapter = FakeEditorWorkbookAdapter(snapshot)

    result = IquavisEditorConflictSyncService(repository=repository, tms_service=service).sync_snapshot(
        snapshot,
        workbook_path=tmp_path / "iquavis_editor.xlsm",
        adapter=adapter,
    )

    written_fields = [name for update in adapter.written_updates for name in update.field_names]
    assert "status" in written_fields, "保存待ちの項目を書き直して黄色へ戻す必要がある"
    assert task_id in result.retried_task_ids
    # 保存待ち自体は解消しない。iQUAVISへ届くまでは待ち状態のまま。
    assert task_id in result.pending_iquavis_save_task_ids


def test_confirmed_pending_is_not_rewritten_again(tmp_path) -> None:
    # iQUAVISへ届いた後まで書き直すと、毎晩無駄な再送が続く。
    service, repository, task_id = _build_service(tmp_path)
    repository.update_task_status(task_id, "Completion", 100.0)
    completed = _props(status="Completion", progress_rate=100)
    repository.set_state(
        IQUAVIS_TASK_CONFLICT_STATE_KEY,
        serialize_conflict_state(
            {
                task_id: IquavisTaskConflict(
                    task_id=task_id,
                    iquavis_id="TASK-1",
                    message="QOLONYの変更をExcelへ書き出しました。",
                    recorded_at=datetime.now(),
                    fields={
                        "status": IquavisTaskConflictField(
                            field="status", baseline="NotStart", qolony="Completion", excel="Completion"
                        )
                    },
                    pending_fields={},
                    workbook_fingerprint=None,
                    sync_state=IQUAVIS_PENDING_SAVE_STATE,
                )
            }
        ),
    )
    # 非表示シートもCompletionになった＝iQUAVISへ保存済み。
    snapshot = IquavisEditorSnapshot(
        task_sync_records=[IquavisTaskSyncRecord("TASK-1", "task", completed, completed)]
    )
    adapter = FakeEditorWorkbookAdapter(snapshot)

    result = IquavisEditorConflictSyncService(repository=repository, tms_service=service).sync_snapshot(
        snapshot,
        workbook_path=tmp_path / "iquavis_editor.xlsm",
        adapter=adapter,
    )

    assert result.retried_task_ids == []
    assert task_id not in result.pending_iquavis_save_task_ids
