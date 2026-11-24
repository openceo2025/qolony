from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.iquavis.editor_forced_import import (
    FIELD_CONFLICT,
    SHARED_SYNC_FIELDS,
    classify_forced_import_task,
    comparable_field_names,
    shared_field_names,
)
from app.iquavis.editor_models import (
    IquavisEditorQolonyUpdate,
    IquavisEditorSnapshot,
    IquavisEditorSyncWriteResult,
    IquavisTaskSyncProperties,
    IquavisTaskSyncRecord,
)
from app.iquavis.sync import (
    IQUAVIS_FORCED_IMPORT_CONFIRMATION,
    IquavisConflictPermissionError,
    IquavisConflictStaleError,
    IQuavisSyncService,
)
from app.iquavis.sync_state import (
    IQUAVIS_CONFLICT_STATE,
    IQUAVIS_TASK_CONFLICT_STATE_KEY,
    parse_conflict_state,
)
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


class FakeAdapter:
    """xlsmを読むだけのアダプタ。強制取込はブックへ書かないので書き込みは失敗にする。"""

    def __init__(self, snapshot: IquavisEditorSnapshot):
        self.snapshot = snapshot

    def load_snapshot(self, workbook_path: Path) -> IquavisEditorSnapshot:
        return self.snapshot

    def write_qolony_updates(self, workbook_path: Path, updates) -> IquavisEditorSyncWriteResult:
        raise AssertionError("強制取込はxlsmへ書き込んではいけない")

    def restore_iquavis_values(self, workbook_path: Path, updates) -> IquavisEditorSyncWriteResult:
        raise AssertionError("強制取込はxlsmへ書き込んではいけない")

    def clear_force_sync_requests(self, workbook_path: Path, iquavis_ids) -> IquavisEditorSyncWriteResult:
        raise AssertionError("強制取込はxlsmへ書き込んではいけない")


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


# ---------------------------------------------------------------------------
# 純粋な3値比較
# ---------------------------------------------------------------------------


def test_shared_fields_exclude_qolony_only_properties() -> None:
    # 見積MHはiQUAVISへ自動送信しないQOLONYローカル項目なので、共有項目に含めない。
    assert "assigned_minutes" not in SHARED_SYNC_FIELDS
    # 進捗率はQOLONYが算出するため比較対象にしない。
    assert "progress_rate" not in shared_field_names(is_parent=False)


def test_parent_task_excludes_aggregated_fields() -> None:
    leaf_fields = shared_field_names(is_parent=False)
    parent_fields = shared_field_names(is_parent=True)
    assert "actual_minutes" in leaf_fields
    assert "status" in leaf_fields
    # 親タスクの実績・状態はQOLONYが子から集計するので共有しない。
    assert "actual_minutes" not in parent_fields
    assert "status" not in parent_fields


def test_missing_workbook_column_is_not_compared() -> None:
    iquavis = replace(_props(), progress_rate=None)
    assert "progress_rate" not in comparable_field_names(iquavis=iquavis, is_parent=False)


def test_iquavis_only_change_is_adopted() -> None:
    baseline = _props(status="NotStart")
    decision = classify_forced_import_task(
        task_id="T1",
        iquavis_id="I1",
        qolony=_props(status="NotStart"),
        iquavis=_props(status="Completion"),
        baseline=baseline,
    )
    assert not decision.is_conflict
    assert [item.field for item in decision.adopt_fields] == ["status"]


def test_qolony_only_change_is_overwritten_by_iquavis() -> None:
    # 強制取込では片側だけの変更は競合にせず、iQUAVIS値で上書きする。
    baseline = _props(name="Base")
    decision = classify_forced_import_task(
        task_id="T1",
        iquavis_id="I1",
        qolony=_props(name="Qolony edited"),
        iquavis=_props(name="Base"),
        baseline=baseline,
    )
    assert not decision.is_conflict
    assert [item.field for item in decision.adopt_fields] == ["name"]
    assert decision.adopt_fields[0].iquavis == "Base"


def test_both_sides_changed_differently_is_conflict() -> None:
    decision = classify_forced_import_task(
        task_id="T1",
        iquavis_id="I1",
        qolony=_props(name="Qolony"),
        iquavis=_props(name="Iquavis"),
        baseline=_props(name="Base"),
    )
    assert decision.is_conflict
    assert decision.adopt_fields == ()
    assert decision.conflict_fields[0].outcome == FIELD_CONFLICT
    assert decision.conflict_fields[0].baseline == "Base"


def test_conflict_task_is_not_partially_applied() -> None:
    # 1項目でも競合したタスクは他項目も適用せず、まるごと採択に回す。
    decision = classify_forced_import_task(
        task_id="T1",
        iquavis_id="I1",
        qolony=_props(name="Qolony", memo="qolony memo"),
        iquavis=_props(name="Iquavis", memo="iquavis memo"),
        baseline=_props(name="Base", memo="base memo"),
    )
    assert decision.is_conflict
    assert decision.adopt_fields == ()
    assert len(decision.conflict_fields) == 2


def test_missing_baseline_adopts_iquavis() -> None:
    # 魚拓が無い初回はiQUAVISを正として取り込み、そこで基準を作る。
    decision = classify_forced_import_task(
        task_id="T1",
        iquavis_id="I1",
        qolony=_props(name="Qolony"),
        iquavis=_props(name="Iquavis"),
        baseline=None,
    )
    assert not decision.is_conflict
    assert [item.field for item in decision.adopt_fields] == ["name"]


def test_equal_values_produce_no_work() -> None:
    decision = classify_forced_import_task(
        task_id="T1",
        iquavis_id="I1",
        qolony=_props(),
        iquavis=_props(),
        baseline=_props(name="Base"),
    )
    assert not decision.is_conflict
    assert decision.adopt_fields == ()


# ---------------------------------------------------------------------------
# サービス結合
# ---------------------------------------------------------------------------


def _build_service(tmp_path):
    today = date.today()
    db_path = tmp_path / "forced_import.db"
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
    workbook_path = tmp_path / "iquavis_editor.xlsm"
    workbook_path.write_bytes(b"workbook")
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
            editor_workbook_path=workbook_path,
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
    tms_service = TmsService(settings=settings, repository=repository)
    sync_service = IQuavisSyncService(repository=repository, tms_service=tms_service)
    return sync_service, repository, tms_service, task.Id


def _snapshot(*, previous: IquavisTaskSyncProperties | None, current: IquavisTaskSyncProperties):
    return IquavisEditorSnapshot(
        task_sync_records=[
            IquavisTaskSyncRecord(
                iquavis_id="TASK-1",
                kind="task",
                previous=previous,
                iquavis_current=current,
            )
        ]
    )


def _apply(sync_service, repository, adapter, operator_id):
    preview = sync_service.preview_editor_forced_import(
        operator_user_id=operator_id,
        adapter=adapter,
    )
    return preview, sync_service.apply_editor_forced_import(
        operator_user_id=operator_id,
        expected_workbook_fingerprint=preview["workbook_fingerprint"],
        confirmation=IQUAVIS_FORCED_IMPORT_CONFIRMATION,
        adapter=adapter,
    )


def test_forced_import_overwrites_qolony_with_iquavis_values(tmp_path) -> None:
    sync_service, repository, _, task_id = _build_service(tmp_path)
    top = repository.get_top_manager()
    repository.update_task_name(task_id, "Qolony edited")
    # 非表示シート(=iQUAVISの正)と表示シートが違う場合、非表示側を採用する。
    adapter = FakeAdapter(
        _snapshot(
            previous=_props(name="From iQUAVIS", status="Completion"),
            current=_props(name="Excel draft", status="NotStart"),
        )
    )

    preview, result = _apply(sync_service, repository, adapter, top.Id)

    assert preview["conflict_task_count"] == 0
    assert preview["updated_task_count"] == 1
    assert result["updated_task_count"] == 1
    assert result["conflict_task_count"] == 0
    task = repository.get_task(task_id)
    assert task.Name == "From iQUAVIS"
    assert task.Status == "Completion"


def test_forced_import_records_baseline_for_next_comparison(tmp_path) -> None:
    sync_service, repository, _, task_id = _build_service(tmp_path)
    top = repository.get_top_manager()
    adapter = FakeAdapter(_snapshot(previous=_props(name="Agreed"), current=_props(name="Agreed")))

    _apply(sync_service, repository, adapter, top.Id)

    baselines = repository.list_iquavis_task_baselines()
    assert len(baselines) == 1
    assert baselines[0].TaskId == task_id
    assert baselines[0].Name == "Agreed"


def test_forced_import_detects_conflict_after_baseline_exists(tmp_path) -> None:
    sync_service, repository, _, task_id = _build_service(tmp_path)
    top = repository.get_top_manager()
    adapter = FakeAdapter(_snapshot(previous=_props(name="Agreed"), current=_props(name="Agreed")))
    _apply(sync_service, repository, adapter, top.Id)

    # 合意後に両側がそれぞれ別の値へ変わると競合になる。
    repository.update_task_name(task_id, "Qolony changed")
    adapter.snapshot = _snapshot(
        previous=_props(name="iQUAVIS changed"),
        current=_props(name="iQUAVIS changed"),
    )

    preview, result = _apply(sync_service, repository, adapter, top.Id)

    assert preview["conflict_task_count"] == 1
    assert result["conflict_task_count"] == 1
    assert result["updated_task_count"] == 0
    # 競合タスクはQOLONY値のまま残し、運用者の採択を待つ。
    assert repository.get_task(task_id).Name == "Qolony changed"
    conflicts = parse_conflict_state(repository.get_state(IQUAVIS_TASK_CONFLICT_STATE_KEY))
    assert conflicts[task_id].sync_state == IQUAVIS_CONFLICT_STATE
    assert conflicts[task_id].fields["name"].baseline == "Agreed"
    assert conflicts[task_id].fields["name"].qolony == "Qolony changed"
    assert conflicts[task_id].fields["name"].excel == "iQUAVIS changed"


def test_forced_import_does_not_conflict_on_qolony_only_property(tmp_path) -> None:
    sync_service, repository, _, task_id = _build_service(tmp_path)
    top = repository.get_top_manager()
    adapter = FakeAdapter(_snapshot(previous=_props(), current=_props()))
    _apply(sync_service, repository, adapter, top.Id)

    # 見積MHはQOLONYローカル項目。両側で違っても競合にしない。
    repository.update_task_assigned_manhours({task_id: 600})
    adapter.snapshot = _snapshot(
        previous=_props(assigned=30),
        current=_props(assigned=30),
    )

    preview, result = _apply(sync_service, repository, adapter, top.Id)

    assert preview["conflict_task_count"] == 0
    assert result["conflict_task_count"] == 0
    assert repository.get_task(task_id).AssignedManHour == 600


def test_forced_import_clears_stale_pending_state(tmp_path) -> None:
    sync_service, repository, _, task_id = _build_service(tmp_path)
    top = repository.get_top_manager()
    repository.set_state(
        IQUAVIS_TASK_CONFLICT_STATE_KEY,
        '{"%s": {"iquavis_id": "TASK-1", "message": "old", "recorded_at": "2026-07-23T00:00:00", '
        '"fields": {}, "pending_fields": {}, "workbook_fingerprint": "stale", '
        '"sync_state": "PendingIquavisSave"}}' % task_id,
    )
    adapter = FakeAdapter(_snapshot(previous=_props(), current=_props()))

    _, result = _apply(sync_service, repository, adapter, top.Id)

    assert result["cleared_state_count"] == 1
    assert parse_conflict_state(repository.get_state(IQUAVIS_TASK_CONFLICT_STATE_KEY)) == {}


def test_forced_import_drops_unresolvable_stale_state_of_untouched_tasks(tmp_path) -> None:
    # ブックから消えた（=同期範囲外の）タスクに残る旧ブック指紋の滞留は、採択できないので捨てる。
    sync_service, repository, _, task_id = _build_service(tmp_path)
    top = repository.get_top_manager()
    repository.set_state(
        IQUAVIS_TASK_CONFLICT_STATE_KEY,
        '{"OTHER-TASK": {"iquavis_id": "TASK-9", "message": "old", '
        '"recorded_at": "2026-07-23T00:00:00", "fields": {}, "pending_fields": {}, '
        '"workbook_fingerprint": "old-fingerprint", "sync_state": "PendingIquavisSave"}}',
    )
    adapter = FakeAdapter(_snapshot(previous=_props(), current=_props()))

    _apply(sync_service, repository, adapter, top.Id)

    assert "OTHER-TASK" not in parse_conflict_state(
        repository.get_state(IQUAVIS_TASK_CONFLICT_STATE_KEY)
    )


def test_forced_import_keeps_qolony_only_tasks(tmp_path) -> None:
    sync_service, repository, tms_service, task_id = _build_service(tmp_path)
    top = repository.get_top_manager()
    member = repository.get_user_by_name("member")
    project = repository.ensure_single_project(
        name="QOLONY",
        start_date=date.today() - timedelta(days=30),
        end_date=date.today() + timedelta(days=365),
        crash_zone_pct=20.0,
    )
    local_only = repository.create_task(
        NewTaskInput(
            ProjectId=project.Id,
            ParentTaskId=None,
            Name="Local only",
            Type="Task",
            AssigneeUserId=member.Id,
            CreatorUserId=top.Id,
            StartDate=date.today(),
            EndDate=date.today() + timedelta(days=3),
            AssignedManHour=60,
            PlannedDeliverables=[],
        )
    )
    adapter = FakeAdapter(_snapshot(previous=_props(), current=_props()))

    preview, result = _apply(sync_service, repository, adapter, top.Id)

    assert preview["local_only_task_count"] == 1
    assert result["local_only_task_count"] == 1
    assert repository.get_task(local_only.Id) is not None


def _conflicted(sync_service, repository, adapter, top_id, task_id):
    """強制取込で1件の競合を作った状態にする。"""
    _apply(sync_service, repository, adapter, top_id)
    repository.update_task_name(task_id, "Qolony changed")
    adapter.snapshot = _snapshot(
        previous=_props(name="iQUAVIS changed"),
        current=_props(name="iQUAVIS changed"),
    )
    _apply(sync_service, repository, adapter, top_id)
    conflicts = parse_conflict_state(repository.get_state(IQUAVIS_TASK_CONFLICT_STATE_KEY))
    return conflicts[task_id]


def test_conflict_can_be_resolved_towards_iquavis_in_one_click(tmp_path) -> None:
    sync_service, repository, _, task_id = _build_service(tmp_path)
    top = repository.get_top_manager()
    adapter = FakeAdapter(_snapshot(previous=_props(name="Agreed"), current=_props(name="Agreed")))
    conflict = _conflicted(sync_service, repository, adapter, top.Id, task_id)

    result = sync_service.resolve_editor_conflict(
        task_id=task_id,
        operator_user_id=top.Id,
        direction="iquavis_to_qolony",
        expected_recorded_at=conflict.recorded_at.isoformat(),
        adapter=adapter,
    )

    assert result["sync_state"] == "Linked"
    assert repository.get_task(task_id).Name == "iQUAVIS changed"
    # 採択で一致したので、次回比較の基準も更新されている。
    baseline = next(item for item in repository.list_iquavis_task_baselines() if item.TaskId == task_id)
    assert baseline.Name == "iQUAVIS changed"
    assert parse_conflict_state(repository.get_state(IQUAVIS_TASK_CONFLICT_STATE_KEY)) == {}


def test_resolved_iquavis_adoption_does_not_come_back_as_conflict(tmp_path) -> None:
    sync_service, repository, _, task_id = _build_service(tmp_path)
    top = repository.get_top_manager()
    adapter = FakeAdapter(_snapshot(previous=_props(name="Agreed"), current=_props(name="Agreed")))
    conflict = _conflicted(sync_service, repository, adapter, top.Id, task_id)
    sync_service.resolve_editor_conflict(
        task_id=task_id,
        operator_user_id=top.Id,
        direction="iquavis_to_qolony",
        expected_recorded_at=conflict.recorded_at.isoformat(),
        adapter=adapter,
    )

    preview, result = _apply(sync_service, repository, adapter, top.Id)

    assert preview["conflict_task_count"] == 0
    assert result["conflict_task_count"] == 0


def test_conflict_resolved_towards_qolony_waits_for_iquavis_save(tmp_path) -> None:
    sync_service, repository, _, task_id = _build_service(tmp_path)
    top = repository.get_top_manager()

    written: list = []

    class WritingAdapter(FakeAdapter):
        def write_qolony_updates(self, workbook_path: Path, updates) -> IquavisEditorSyncWriteResult:
            written.extend(updates)
            # Excelへ黄色で書いた状態を表示シートへ反映する。非表示シートは保存されるまで変わらない。
            record = self.snapshot.task_sync_records[0]
            values = {
                field_name: getattr(updates[0].properties, field_name)
                for field_name in updates[0].field_names
            }
            self.snapshot = replace(
                self.snapshot,
                task_sync_records=[replace(record, iquavis_current=replace(record.iquavis_current, **values))],
            )
            return IquavisEditorSyncWriteResult(updated_ids=[update.iquavis_id for update in updates])

    adapter = WritingAdapter(_snapshot(previous=_props(name="Agreed"), current=_props(name="Agreed")))
    conflict = _conflicted(sync_service, repository, adapter, top.Id, task_id)

    result = sync_service.resolve_editor_conflict(
        task_id=task_id,
        operator_user_id=top.Id,
        direction="qolony_to_iquavis",
        expected_recorded_at=conflict.recorded_at.isoformat(),
        adapter=adapter,
    )

    # QOLONY採択はExcelへ書き出すだけ。iQUAVISへ保存されるまで解消にしない。
    assert result["sync_state"] == "PendingIquavisSave"
    assert written and written[0].iquavis_id == "TASK-1"
    assert repository.get_task(task_id).Name == "Qolony changed"
    conflicts = parse_conflict_state(repository.get_state(IQUAVIS_TASK_CONFLICT_STATE_KEY))
    assert conflicts[task_id].sync_state == "PendingIquavisSave"


def test_conflict_is_visible_on_task_objects_in_views(tmp_path) -> None:
    # WBS・Today などのタスク表示はTask DTOのIQuavisSyncStateを見るので、そこに出ることを担保する。
    sync_service, repository, tms_service, task_id = _build_service(tmp_path)
    top = repository.get_top_manager()
    adapter = FakeAdapter(_snapshot(previous=_props(name="Agreed"), current=_props(name="Agreed")))
    _conflicted(sync_service, repository, adapter, top.Id, task_id)

    task_dto = next(item for item in tms_service.list_tasks() if item.Id == task_id)
    assert task_dto.IQuavisSyncState == "Conflict"
    assert task_dto.IQuavisConflictMessage

    listed = sync_service.list_editor_conflicts()
    entry = next(item for item in listed["items"] if item["task_id"] == task_id)
    assert entry["sync_state"] == "Conflict"
    # 3値がそろっていないと採択画面が判断材料を出せない。
    field = next(item for item in entry["fields"] if item["field"] == "name")
    assert field["baseline"] == "Agreed"
    assert field["qolony"] == "Qolony changed"
    assert field["excel"] == "iQUAVIS changed"


def test_forced_import_requires_top_manager(tmp_path) -> None:
    sync_service, repository, _, _ = _build_service(tmp_path)
    member = repository.get_user_by_name("member")
    adapter = FakeAdapter(_snapshot(previous=_props(), current=_props()))
    with pytest.raises(IquavisConflictPermissionError):
        sync_service.preview_editor_forced_import(operator_user_id=member.Id, adapter=adapter)


def test_forced_import_rejects_wrong_confirmation(tmp_path) -> None:
    sync_service, repository, _, _ = _build_service(tmp_path)
    top = repository.get_top_manager()
    adapter = FakeAdapter(_snapshot(previous=_props(), current=_props()))
    preview = sync_service.preview_editor_forced_import(operator_user_id=top.Id, adapter=adapter)
    with pytest.raises(UserInputError):
        sync_service.apply_editor_forced_import(
            operator_user_id=top.Id,
            expected_workbook_fingerprint=preview["workbook_fingerprint"],
            confirmation="NO",
            adapter=adapter,
        )


def test_forced_import_rejects_stale_fingerprint(tmp_path) -> None:
    sync_service, repository, _, _ = _build_service(tmp_path)
    top = repository.get_top_manager()
    adapter = FakeAdapter(_snapshot(previous=_props(), current=_props()))
    with pytest.raises(IquavisConflictStaleError):
        sync_service.apply_editor_forced_import(
            operator_user_id=top.Id,
            expected_workbook_fingerprint="different",
            confirmation=IQUAVIS_FORCED_IMPORT_CONFIRMATION,
            adapter=adapter,
        )
