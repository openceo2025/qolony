from datetime import date

from openpyxl import Workbook, load_workbook

from app.iquavis.link_targets import read_sync_targets
from app.tms_core.config import (
    AppConfig,
    CalendarConfig,
    IQuavisConfig,
    Settings,
    TeamConfig,
    UsersConfig,
)
from app.tms_core.db.repository import TmsRepository
from app.tms_core.db.session import create_all_tables, create_session_factory, create_sqlite_engine
from app.tms_core.services.core import TmsService


def _settings(tmp_path, workbook_path):
    return Settings(
        app=AppConfig(
            db_path=tmp_path / "members.db",
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
            link_targets_workbook_path=workbook_path,
        ),
        team=TeamConfig(
            project_name="QOLONY",
            project_start_date=date(2026, 4, 1),
            project_end_date=date(2026, 12, 31),
            default_education_rate_monthly=0.0,
        ),
        users=UsersConfig(top_manager="", members=[]),
        calendar=CalendarConfig(holidays=set()),
        initial_tasks=[],
        split_templates=[],
    )


def test_member_link_target_import_adds_and_updates_members(tmp_path) -> None:
    db_path = tmp_path / "members.db"
    workbook_path = tmp_path / "iquavis_link_targets.xlsx"
    engine = create_sqlite_engine(db_path)
    create_all_tables(engine)
    repository = TmsRepository(create_session_factory(engine))
    repository.ensure_users(
        names=["existing"],
        top_manager_name="existing",
        free_time_rate_default=0.2,
    )
    existing = repository.get_user_by_name("existing")
    assert existing is not None

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "QOLONY_members"
    sheet.append(
        [
            "use",
            "qolony_user_id",
            "nickname",
            "full_name",
            "login_id",
            "iquavis_user_id",
            "is_top_manager",
            "is_active",
            "note",
        ]
    )
    sheet.append(["1", existing.Id, "既存ニックネーム", "既存 本名", "EX001", "IQ-EX", "0", "0", "update"])
    sheet.append(["1", "", "新規ニックネーム", "新規 本名", "NEW001", "IQ-NEW", "1", "1", "create"])
    workbook.save(workbook_path)

    service = TmsService(settings=_settings(tmp_path, workbook_path), repository=repository)
    result = service.import_member_link_targets()

    assert result["read"] == 2
    assert result["created"] == 1
    assert result["updated"] == 1

    updated = repository.get_user(existing.Id)
    created = repository.get_user_by_login_id("NEW001")
    assert updated is not None
    assert created is not None
    assert updated.Nickname == "既存ニックネーム"
    assert updated.FullName == "既存 本名"
    assert updated.LoginId == "EX001"
    assert updated.IQuavisUserId == "IQ-EX"
    assert updated.IsActive is False
    assert created.Nickname == "新規ニックネーム"
    assert created.IsTopManager is True

    saved = load_workbook(workbook_path, data_only=True)
    assert "QOLONY_members" in saved.sheetnames
    assert saved["QOLONY_members"].max_row == 3


def test_link_target_workbook_reads_task_and_domain_targets(tmp_path) -> None:
    workbook_path = tmp_path / "iquavis_link_targets.xlsx"
    workbook = Workbook()
    tasks = workbook.active
    tasks.title = "QOLONY_linked_tasks"
    tasks.append(["use", "iquavis_task_id", "qolony_task_id", "qolony_parent_task_id", "force_sync", "note"])
    tasks.append(["1", "TASK-1", "LOCAL-1", "LOCAL-PARENT", "qolony→iquavis", ""])
    domains = workbook.create_sheet("QOLONY_linked_task_domains")
    domains.append(["use", "iquavis_task_domain_id", "qolony_task_id", "qolony_parent_task_id", "force_sync", "note"])
    domains.append(["1", "DOMAIN-1", "", "LOCAL-ROOT", "iquavis→qolony", ""])
    workbook.save(workbook_path)

    targets = {target.iquavis_id: target for target in read_sync_targets(workbook_path)}

    assert targets["TASK-1"].kind == "task"
    assert targets["TASK-1"].force_sync == "qolony_to_iquavis"
    assert targets["TASK-1"].qolony_task_id == "LOCAL-1"
    assert targets["TASK-1"].qolony_parent_task_id == "LOCAL-PARENT"
    assert targets["DOMAIN-1"].kind == "task_domain"
    assert targets["DOMAIN-1"].force_sync == "iquavis_to_qolony"
