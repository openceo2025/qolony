from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import os
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class AppConfig:
    db_path: Path
    crash_zone_pct: float
    task_signal_capacity_minutes_per_day: int
    seed_initial_tasks_on_empty_db: bool
    seed_demo_data_on_empty_db: bool
    free_time_rate_default: float
    regular_minutes_per_day: int
    mobile_window_width: int
    mobile_window_height: int
    daily_snapshot_hour: int
    daily_snapshot_minute: int


@dataclass(frozen=True)
class UiConfig:
    language: str = "en"
    messages: dict[str, Any] = field(default_factory=dict)
    fallback_messages: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IQuavisConfig:
    enabled: bool
    base_url: str
    username: str
    password: str
    timeout_seconds: float
    max_retries: int
    retry_base_seconds: int
    poll_interval_seconds: int
    allowed_project_ids: list[str]
    editor_workbook_path: Path | None = None
    link_targets_workbook_path: Path | None = None
    daily_sync_hour: int = 2
    daily_sync_minute: int = 0
    excel_server_save_enabled: bool = False
    excel_server_save_expected_project_count: int = 3
    excel_server_save_timeout_seconds: int = 900


@dataclass(frozen=True)
class UserMemberConfig:
    name: str
    login_id: str | None = None
    iquavis_user_id: str | None = None


@dataclass(frozen=True)
class TeamConfig:
    project_name: str
    project_start_date: date
    project_end_date: date
    default_education_rate_monthly: float


@dataclass(frozen=True)
class UsersConfig:
    top_manager: str
    members: list[str | UserMemberConfig]
    passwords: dict[str, str] = field(default_factory=dict)

    def member_names(self) -> list[str]:
        result: list[str] = []
        for member in self.members:
            if isinstance(member, UserMemberConfig):
                result.append(member.name)
            else:
                text = str(member).strip()
                if text:
                    result.append(text)
        return result

    def member_login_ids_by_name(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for member in self.members:
            if isinstance(member, UserMemberConfig) and member.login_id:
                result[member.name] = member.login_id
        return result

    def member_iquavis_user_ids_by_name(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for member in self.members:
            if isinstance(member, UserMemberConfig) and member.iquavis_user_id:
                result[member.name] = member.iquavis_user_id
        return result


@dataclass(frozen=True)
class CalendarConfig:
    holidays: set[date]


@dataclass(frozen=True)
class TaskSeed:
    name: str
    assignee: str
    creator: str
    assigned_minutes: int
    due_date: date
    planned_deliverables: list[dict[str, str]]
    children: list["TaskSeed"]


@dataclass(frozen=True)
class SplitTemplateRow:
    task_name: str
    ratio: float


@dataclass(frozen=True)
class SplitTemplate:
    title: str
    rows: list[SplitTemplateRow]


@dataclass(frozen=True)
class Settings:
    app: AppConfig
    iquavis: IQuavisConfig
    team: TeamConfig
    users: UsersConfig
    calendar: CalendarConfig
    initial_tasks: list[TaskSeed]
    split_templates: list[SplitTemplate]
    ui: UiConfig = field(default_factory=UiConfig)


def _parse_date(value: str | date | datetime) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise TypeError(f"日付型が不正です: {type(value)}")


def _parse_task_seed(item: dict[str, Any]) -> TaskSeed:
    return TaskSeed(
        name=item["name"],
        assignee=item["assignee"],
        creator=item.get("creator", item["assignee"]),
        assigned_minutes=int(item["assigned_minutes"]),
        due_date=_parse_date(item["due_date"]),
        planned_deliverables=list(item.get("planned_deliverables", [])),
        children=[_parse_task_seed(child) for child in item.get("children", [])],
    )


def _parse_ratio(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("%"):
            text = text[:-1]
        return float(text)
    raise TypeError(f"比率型が不正です: {type(value)}")


def _parse_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    raise TypeError(f"真偽値が不正です: {value!r}")


def _parse_string_list(values: Any) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise TypeError(f"文字列配列が不正です: {type(values)}")
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if text:
            result.append(text)
    return result


def _parse_optional_path(value: Any) -> Path | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return Path(text)


def _parse_hour(value: Any, *, default: int) -> int:
    hour = int(value if value is not None else default)
    if hour < 0 or hour > 23:
        raise ValueError(f"hour must be 0..23: {hour}")
    return hour


def _parse_minute(value: Any, *, default: int) -> int:
    minute = int(value if value is not None else default)
    if minute < 0 or minute > 59:
        raise ValueError(f"minute must be 0..59: {minute}")
    return minute


def _read_sync_time_from_env() -> tuple[int, int] | None:
    text = os.getenv("QOLONY_IQUAVIS_SYNC_TIME", "").strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) != 2:
        raise ValueError("QOLONY_IQUAVIS_SYNC_TIME must be HH:MM")
    return _parse_hour(parts[0], default=2), _parse_minute(parts[1], default=0)


def _parse_user_member(value: Any) -> UserMemberConfig:
    if isinstance(value, dict):
        name = str(value.get("name", "")).strip()
        if not name:
            raise ValueError("users.members[].name is required")
        login_id = str(value.get("login_id", "")).strip() or None
        iquavis_user_id = str(value.get("iquavis_user_id", "")).strip() or None
        return UserMemberConfig(
            name=name,
            login_id=login_id,
            iquavis_user_id=iquavis_user_id,
        )

    text = str(value).strip()
    if not text:
        raise ValueError("users.members[] contains an empty name")
    return UserMemberConfig(name=text)


def _load_split_templates(path: Path | str = "config/split_templates.yaml") -> list[SplitTemplate]:
    config_path = Path(path)
    if not config_path.exists():
        return []

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    templates_raw = raw.get("templates", [])
    result: list[SplitTemplate] = []

    for item in templates_raw:
        rows = [
            SplitTemplateRow(
                task_name=str(row.get("task_name", "")).strip(),
                ratio=_parse_ratio(row.get("ratio", 0)),
            )
            for row in item.get("rows", [])
            if str(row.get("task_name", "")).strip()
        ]
        result.append(
            SplitTemplate(
                title=str(item.get("title", "")).strip(),
                rows=rows,
            )
        )

    return [template for template in result if template.title and template.rows]


def _load_calendar(path: Path | str = "config/calendar.yaml") -> CalendarConfig:
    config_path = Path(path)
    if not config_path.exists():
        return CalendarConfig(holidays=set())

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    holidays_raw = raw.get("holidays", [])
    holidays: set[date] = set()
    for item in holidays_raw:
        holidays.add(_parse_date(item))
    return CalendarConfig(holidays=holidays)


def _load_ui_messages(base_dir: Path, language: str) -> tuple[dict[str, Any], dict[str, Any]]:
    i18n_dir = base_dir / "i18n"
    fallback_path = i18n_dir / "en.yaml"
    language_path = i18n_dir / f"{language}.yaml"

    if not fallback_path.exists():
        raise FileNotFoundError(f"fallback i18n file not found: {fallback_path}")

    fallback_messages = yaml.safe_load(fallback_path.read_text(encoding="utf-8")) or {}
    if not isinstance(fallback_messages, dict):
        raise ValueError("config/i18n/en.yaml must be a mapping")

    if language == "en":
        return fallback_messages, fallback_messages

    if not language_path.exists():
        raise FileNotFoundError(f"i18n file not found for language '{language}': {language_path}")

    messages = yaml.safe_load(language_path.read_text(encoding="utf-8")) or {}
    if not isinstance(messages, dict):
        raise ValueError(f"config/i18n/{language}.yaml must be a mapping")

    return messages, fallback_messages


def load_settings(path: Path | str = "config/settings.yaml", config_dir: Path | str | None = None) -> Settings:
    config_path = Path(path)
    # 分割テンプレート・カレンダー・言語ファイルはアプリ側のもの。設定ファイルだけ外に置けるようにする。
    support_dir = Path(config_dir) if config_dir is not None else config_path.parent
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    app = raw["app"]
    integrations = raw.get("integrations", {}) or {}
    iquavis_raw = integrations.get("iquavis", {}) or {}
    ui_raw = raw.get("ui", {}) or {}
    team = raw["team"]
    users = raw.get("users", {}) or {}

    members = [_parse_user_member(item) for item in list(users.get("members", []))]
    member_names = [member.name for member in members]
    top_manager = str(users.get("top_manager", "")).strip()
    raw_passwords = users.get("passwords", {})
    if raw_passwords is None:
        raw_passwords = {}
    if not isinstance(raw_passwords, dict):
        raise ValueError("users.passwords must be a mapping: {user_name: password}")
    passwords = {
        str(name).strip(): str(password)
        for name, password in raw_passwords.items()
        if str(name).strip() and str(password) != ""
    }

    if top_manager and top_manager not in member_names:
        raise ValueError("users.top_manager は users.members に含めてください")

    if len(set(member_names)) != len(member_names):
        raise ValueError("users.members に重複があります")

    login_ids = [member.login_id for member in members if member.login_id]
    if len(set(login_ids)) != len(login_ids):
        raise ValueError("users.members[].login_id に重複があります")

    iquavis_user_ids = [member.iquavis_user_id for member in members if member.iquavis_user_id]
    if len(set(iquavis_user_ids)) != len(iquavis_user_ids):
        raise ValueError("users.members[].iquavis_user_id に重複があります")

    unknown_password_users = [name for name in passwords if member_names and name not in member_names]
    if unknown_password_users:
        names = ", ".join(sorted(unknown_password_users))
        raise ValueError(f"users.passwords contains unknown users not in members: {names}")

    split_templates_path = support_dir / "split_templates.yaml"
    calendar_path = support_dir / "calendar.yaml"
    ui_language = str(ui_raw.get("language", "en")).strip().lower() or "en"
    ui_messages, fallback_messages = _load_ui_messages(support_dir, ui_language)

    db_path = Path(os.getenv("QOLONY_DB_PATH", "").strip() or app["db_path"])
    editor_workbook_path = _parse_optional_path(
        os.getenv("QOLONY_IQUAVIS_EDITOR_PATH", "").strip()
        or iquavis_raw.get("editor_workbook_path")
        or iquavis_raw.get("workbook_path")
    )
    iquavis_enabled = os.getenv("QOLONY_IQUAVIS_ENABLED", "").strip()
    link_targets_workbook_path = _parse_optional_path(
        os.getenv("QOLONY_IQUAVIS_LINK_TARGETS_PATH", "").strip()
        or iquavis_raw.get("link_targets_workbook_path")
    )
    sync_time_from_env = _read_sync_time_from_env()
    if sync_time_from_env is None:
        daily_sync_hour = _parse_hour(iquavis_raw.get("daily_sync_hour"), default=2)
        daily_sync_minute = _parse_minute(iquavis_raw.get("daily_sync_minute"), default=0)
    else:
        daily_sync_hour, daily_sync_minute = sync_time_from_env
    excel_server_save_enabled_raw = os.getenv("QOLONY_IQUAVIS_EXCEL_SERVER_SAVE_ENABLED", "").strip()
    excel_server_save_expected_project_count = int(
        iquavis_raw.get("excel_server_save_expected_project_count", 3)
    )
    excel_server_save_timeout_seconds = int(iquavis_raw.get("excel_server_save_timeout_seconds", 900))
    if excel_server_save_expected_project_count <= 0:
        raise ValueError("integrations.iquavis.excel_server_save_expected_project_count must be positive")
    if excel_server_save_timeout_seconds <= 0:
        raise ValueError("integrations.iquavis.excel_server_save_timeout_seconds must be positive")

    return Settings(
        app=AppConfig(
            db_path=db_path,
            crash_zone_pct=float(app["crash_zone_pct"]),
            task_signal_capacity_minutes_per_day=int(app.get("task_signal_capacity_minutes_per_day", 240)),
            seed_initial_tasks_on_empty_db=_parse_bool(app.get("seed_initial_tasks_on_empty_db"), default=False),
            seed_demo_data_on_empty_db=_parse_bool(app.get("seed_demo_data_on_empty_db"), default=False),
            free_time_rate_default=float(app["free_time_rate_default"]),
            regular_minutes_per_day=int(app["regular_minutes_per_day"]),
            mobile_window_width=int(app["mobile_window_width"]),
            mobile_window_height=int(app["mobile_window_height"]),
            daily_snapshot_hour=int(app["daily_snapshot_hour"]),
            daily_snapshot_minute=int(app["daily_snapshot_minute"]),
        ),
        iquavis=IQuavisConfig(
            enabled=_parse_bool(iquavis_enabled if iquavis_enabled else iquavis_raw.get("enabled"), default=False),
            base_url=str(iquavis_raw.get("base_url", "")).rstrip("/"),
            username=str(iquavis_raw.get("username", "")).strip(),
            password=str(iquavis_raw.get("password", "")),
            timeout_seconds=float(iquavis_raw.get("timeout_seconds", 10.0)),
            max_retries=int(iquavis_raw.get("max_retries", 5)),
            retry_base_seconds=int(iquavis_raw.get("retry_base_seconds", 30)),
            poll_interval_seconds=int(iquavis_raw.get("poll_interval_seconds", 60)),
            allowed_project_ids=_parse_string_list(iquavis_raw.get("allowed_project_ids")),
            editor_workbook_path=editor_workbook_path,
            link_targets_workbook_path=link_targets_workbook_path,
            daily_sync_hour=daily_sync_hour,
            daily_sync_minute=daily_sync_minute,
            excel_server_save_enabled=_parse_bool(
                excel_server_save_enabled_raw
                if excel_server_save_enabled_raw
                else iquavis_raw.get("excel_server_save_enabled"),
                default=False,
            ),
            excel_server_save_expected_project_count=excel_server_save_expected_project_count,
            excel_server_save_timeout_seconds=excel_server_save_timeout_seconds,
        ),
        team=TeamConfig(
            project_name=team["project_name"],
            project_start_date=_parse_date(team["project_start_date"]),
            project_end_date=_parse_date(team["project_end_date"]),
            default_education_rate_monthly=float(team["default_education_rate_monthly"]),
        ),
        users=UsersConfig(top_manager=top_manager, members=members, passwords=passwords),
        calendar=_load_calendar(path=calendar_path),
        initial_tasks=[_parse_task_seed(item) for item in raw.get("initial_tasks", [])],
        split_templates=_load_split_templates(path=split_templates_path),
        ui=UiConfig(
            language=ui_language,
            messages=ui_messages,
            fallback_messages=fallback_messages,
        ),
    )
