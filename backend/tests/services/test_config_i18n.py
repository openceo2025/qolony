from pathlib import Path

from app.tms_core.config import load_settings


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_load_settings_reads_ui_language_and_fallback_messages(tmp_path: Path) -> None:
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
  top_manager: 山田
  members:
    - 山田
ui:
  language: ja
""".strip(),
    )
    _write_text(config_dir / "calendar.yaml", "holidays: []\n")
    _write_text(config_dir / "split_templates.yaml", "templates: []\n")
    _write_text(
        config_dir / "i18n" / "en.yaml",
        """
app:
  tabs:
    today: Today Tasks
common:
  status: Status
""".strip(),
    )
    _write_text(
        config_dir / "i18n" / "ja.yaml",
        """
app:
  tabs:
    today: 本日のタスク
""".strip(),
    )

    settings = load_settings(config_dir / "settings.yaml")

    assert settings.ui.language == "ja"
    assert settings.ui.messages["app"]["tabs"]["today"] == "本日のタスク"
    assert settings.ui.fallback_messages["common"]["status"] == "Status"


def test_load_settings_supports_extended_user_directory(tmp_path: Path) -> None:
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
  top_manager: 山田
  members:
    - name: 山田
      login_id: U0001
      iquavis_user_id: 00000000-0000-4000-8000-000000000001
    - name: 中村(一)
  passwords:
    山田: u0001
ui:
  language: en
""".strip(),
    )
    _write_text(config_dir / "calendar.yaml", "holidays: []\n")
    _write_text(config_dir / "split_templates.yaml", "templates: []\n")
    _write_text(config_dir / "i18n" / "en.yaml", "{}\n")

    settings = load_settings(config_dir / "settings.yaml")

    assert settings.users.member_names() == ["山田", "中村(一)"]
    assert settings.users.member_login_ids_by_name() == {"山田": "U0001"}
    assert settings.users.member_iquavis_user_ids_by_name() == {
        "山田": "00000000-0000-4000-8000-000000000001"
    }
