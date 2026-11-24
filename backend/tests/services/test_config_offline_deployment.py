from pathlib import Path

from app.tms_core.config import load_settings


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_load_settings_reads_offline_deployment_overrides(tmp_path: Path, monkeypatch) -> None:
    config_dir = tmp_path / "config"
    _write_text(
        config_dir / "settings.yaml",
        """
app:
  db_path: data/test.db
  crash_zone_pct: 20
  task_signal_capacity_minutes_per_day: 240
  seed_initial_tasks_on_empty_db: false
  seed_demo_data_on_empty_db: false
  free_time_rate_default: 0.2
  regular_minutes_per_day: 480
  mobile_window_width: 460
  mobile_window_height: 800
  daily_snapshot_hour: 0
  daily_snapshot_minute: 5
team:
  project_name: QOLONY
  project_start_date: 2026-04-01
  project_end_date: 2026-04-30
  default_education_rate_monthly: 0.1
users:
  top_manager: 菴占陸
  members:
    - 菴占陸
integrations:
  iquavis:
    enabled: true
    base_url: http://iquavis.example.local/iquavis-api
    username: u0001
    password: u0001
    editor_workbook_path: C:/qolony/iquavis_editor.xlsm
    daily_sync_hour: 3
    daily_sync_minute: 15
ui:
  language: en
""".strip(),
    )
    _write_text(config_dir / "calendar.yaml", "holidays: []\n")
    _write_text(config_dir / "split_templates.yaml", "templates: []\n")
    _write_text(config_dir / "i18n" / "en.yaml", "{}\n")

    monkeypatch.setenv("QOLONY_DB_PATH", "C:/QOLONY_DATA/team.db")
    monkeypatch.setenv("QOLONY_IQUAVIS_EDITOR_PATH", "D:/work/iquavis_editor.xlsm")
    monkeypatch.setenv("QOLONY_IQUAVIS_SYNC_TIME", "04:45")
    monkeypatch.setenv("QOLONY_IQUAVIS_ENABLED", "0")
    monkeypatch.setenv("QOLONY_IQUAVIS_EXCEL_SERVER_SAVE_ENABLED", "1")

    settings = load_settings(config_dir / "settings.yaml")

    assert settings.app.db_path == Path("C:/QOLONY_DATA/team.db")
    assert settings.iquavis.enabled is False
    assert settings.iquavis.editor_workbook_path == Path("D:/work/iquavis_editor.xlsm")
    assert settings.iquavis.daily_sync_hour == 4
    assert settings.iquavis.daily_sync_minute == 45
    assert settings.iquavis.excel_server_save_enabled is True
    assert settings.iquavis.excel_server_save_expected_project_count == 3
