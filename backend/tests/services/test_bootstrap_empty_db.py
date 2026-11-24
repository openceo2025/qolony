from __future__ import annotations

from dataclasses import replace
from datetime import date

from app.tms_core.config import AppConfig, CalendarConfig, IQuavisConfig, Settings, TeamConfig, UsersConfig
from app.tms_core.db.repository import TmsRepository
from app.tms_core.db.session import create_all_tables, create_session_factory, create_sqlite_engine
from app.tms_core.services.bootstrap import bootstrap_data


def _settings(db_path, *, demo: bool) -> Settings:
    return Settings(
        app=AppConfig(
            db_path=db_path,
            crash_zone_pct=20.0,
            task_signal_capacity_minutes_per_day=240,
            seed_initial_tasks_on_empty_db=False,
            seed_demo_data_on_empty_db=demo,
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
            project_start_date=date(2026, 1, 1),
            project_end_date=date(2026, 12, 31),
            default_education_rate_monthly=0.1,
        ),
        users=UsersConfig(top_manager="山田", members=["山田", "鈴木"]),
        calendar=CalendarConfig(holidays=set()),
        initial_tasks=[],
        split_templates=[],
    )


def _repository(tmp_path) -> TmsRepository:
    engine = create_sqlite_engine(tmp_path / "bootstrap.db")
    create_all_tables(engine)
    return TmsRepository(create_session_factory(engine))


def test_empty_db_gets_members_from_settings_and_demo_tasks(tmp_path) -> None:
    """iQUAVIS を使わない新規導入でも、初回起動で操作できる状態になる。"""
    repository = _repository(tmp_path)
    bootstrap_data(settings=_settings(tmp_path / "bootstrap.db", demo=True), repository=repository)
    users = {user.Name: user for user in repository.list_users()}
    assert set(users) == {"山田", "鈴木"}
    assert users["山田"].IsTopManager is True
    project = repository.ensure_single_project(
        name="QOLONY", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31), crash_zone_pct=20.0
    )
    assert repository.count_tasks(project.Id) > 0


def test_existing_users_are_not_replaced_by_settings(tmp_path) -> None:
    """既に利用者がいるDB（会社の運用DBなど）では設定ファイルのメンバーで上書きしない。"""
    repository = _repository(tmp_path)
    repository.ensure_users(names=["既存の人"], top_manager_name="既存の人", free_time_rate_default=0.2)
    settings = _settings(tmp_path / "bootstrap.db", demo=False)
    bootstrap_data(settings=replace(settings), repository=repository)
    assert [user.Name for user in repository.list_users()] == ["既存の人"]
