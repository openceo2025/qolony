from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import shutil
import sqlite3

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.tms_core.db.session import build_sqlite_url


REVISION_0001 = "0001_initial"
REVISION_0002 = "0002_add_today_fields"
REVISION_0003 = "0003_add_task_memo"
REVISION_0004 = "0004_add_daily_actual_history"
REVISION_0005 = "0005_drop_actual_start_add_v_average"
REVISION_0006 = "0006_add_child_dependency_mode"
REVISION_0007 = "0007_add_iquavis_sync_support"
REVISION_0008 = "0008_add_iquavis_user_id_to_users"
REVISION_0009 = "0009_add_member_view_support"
REVISION_0010 = "0010_add_member_day_leave_type"
REVISION_0011 = "0011_add_member_identity_fields"
REVISION_0012 = "0012_add_wbs_sort_order"
REVISION_0013 = "0013_add_iquavis_task_baselines"
REVISION_0014 = "0014_add_task_updated_by"

MIGRATION_ORDER = (
    REVISION_0001,
    REVISION_0002,
    REVISION_0003,
    REVISION_0004,
    REVISION_0005,
    REVISION_0006,
    REVISION_0007,
    REVISION_0008,
    REVISION_0009,
    REVISION_0010,
    REVISION_0011,
    REVISION_0012,
    REVISION_0013,
    REVISION_0014,
)
REVISION_RANK = {revision: index for index, revision in enumerate(MIGRATION_ORDER)}


@dataclass(frozen=True)
class DbConversionResult:
    source_path: Path
    target_path: Path
    backup_path: Path | None
    inferred_revision: str | None
    initial_revision: str | None
    stamped_revision: str | None
    final_revision: str | None
    head_revision: str



def _build_alembic_config(project_root: Path, db_path: Path) -> Config:
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", build_sqlite_url(db_path))
    return cfg



def get_head_revision(project_root: Path) -> str:
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()
    if head is None:
        raise RuntimeError("alembic head revision not found")
    return head



def _read_schema_snapshot(db_path: Path) -> dict[str, set[str]]:
    with sqlite3.connect(str(db_path)) as conn:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        ]
        snapshot: dict[str, set[str]] = {}
        for table in tables:
            cols = {
                row[1]
                for row in conn.execute(f"PRAGMA table_info('{table}')").fetchall()
            }
            snapshot[table] = cols
        return snapshot



def read_alembic_revision(db_path: Path) -> str | None:
    snapshot = _read_schema_snapshot(db_path)
    if "alembic_version" not in snapshot:
        return None

    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute("SELECT version_num FROM alembic_version").fetchall()
    if not rows:
        return None
    if len(rows) > 1:
        raise RuntimeError("multi-head alembic_version is not supported by this utility")
    return str(rows[0][0])



def infer_revision_from_schema(db_path: Path) -> str | None:
    snapshot = _read_schema_snapshot(db_path)
    if not snapshot:
        return None

    tasks = snapshot.get("Tasks")
    users = snapshot.get("Users")
    if tasks is None:
        return None

    member_day_configs = snapshot.get("MemberDayConfigs")
    if "UpdatedByUserId" in tasks:
        return REVISION_0014

    if "IQuavisTaskBaselines" in snapshot:
        return REVISION_0013

    if "WbsSortOrder" in tasks:
        return REVISION_0012

    if users is not None and "Nickname" in users and "FullName" in users and "IsActive" in users:
        return REVISION_0011

    if member_day_configs is not None and "LeaveType" in member_day_configs:
        return REVISION_0010

    if "MemberProfiles" in snapshot and member_day_configs is not None:
        return REVISION_0009

    if users is not None and "IQuavisUserId" in users:
        return REVISION_0008

    if "IQuavisTaskId" in tasks:
        return REVISION_0007

    if "ChildDependencyMode" in tasks:
        return REVISION_0006

    if "DailyProgressSnapshots" in snapshot:
        progress_snapshot = snapshot["DailyProgressSnapshots"]
        if "VAverage" in progress_snapshot:
            return REVISION_0005
        return REVISION_0004

    if "Memo" in tasks:
        return REVISION_0003

    if "TodayOverride" in tasks and "TodaySortOrder" in tasks:
        return REVISION_0002

    return REVISION_0001



def _revision_rank(revision: str | None) -> int:
    if revision is None:
        return -1
    return REVISION_RANK.get(revision, -1)



def _decide_stamp_revision(current_revision: str | None, inferred_revision: str | None) -> str | None:
    if inferred_revision is None:
        return None

    if current_revision is None:
        return inferred_revision

    current_rank = _revision_rank(current_revision)
    inferred_rank = _revision_rank(inferred_revision)

    if current_rank < 0:
        return inferred_revision

    # 既存DBの版情報が実スキーマより古い場合は、重複DDLを避けるため実スキーマ側へ合わせる。
    if inferred_rank > current_rank:
        return inferred_revision

    return None



def _backup_path_for(db_path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return db_path.with_name(f"{db_path.stem}.preconvert_{timestamp}{db_path.suffix}")



def _ensure_child_dependency_mode_not_blank(db_path: Path) -> None:
    snapshot = _read_schema_snapshot(db_path)
    if "Tasks" not in snapshot:
        return
    if "ChildDependencyMode" not in snapshot["Tasks"]:
        return

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE Tasks "
            "SET ChildDependencyMode='Serial' "
            "WHERE ChildDependencyMode IS NULL OR TRIM(ChildDependencyMode)=''"
        )
        conn.commit()



def convert_legacy_db(
    source_db_path: Path,
    project_root: Path,
    *,
    target_db_path: Path | None = None,
    overwrite: bool = False,
    make_backup: bool = True,
) -> DbConversionResult:
    source = source_db_path.resolve()
    if not source.exists():
        raise FileNotFoundError(f"source database not found: {source}")

    project_root = project_root.resolve()
    if not (project_root / "alembic.ini").exists():
        raise FileNotFoundError(f"alembic.ini not found: {project_root / 'alembic.ini'}")

    target = source if target_db_path is None else target_db_path.resolve()

    backup_path: Path | None = None
    if target == source:
        if make_backup:
            backup_path = _backup_path_for(target)
            shutil.copy2(target, backup_path)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if not overwrite:
                raise FileExistsError(f"target database already exists: {target}")
            if make_backup:
                backup_path = _backup_path_for(target)
                shutil.copy2(target, backup_path)
        shutil.copy2(source, target)

    initial_revision = read_alembic_revision(target)
    inferred_revision = infer_revision_from_schema(target)
    stamp_revision = _decide_stamp_revision(initial_revision, inferred_revision)

    cfg = _build_alembic_config(project_root=project_root, db_path=target)
    if stamp_revision is not None:
        command.stamp(cfg, stamp_revision)

    command.upgrade(cfg, "head")
    _ensure_child_dependency_mode_not_blank(target)

    final_revision = read_alembic_revision(target)
    head_revision = get_head_revision(project_root)

    return DbConversionResult(
        source_path=source,
        target_path=target,
        backup_path=backup_path,
        inferred_revision=inferred_revision,
        initial_revision=initial_revision,
        stamped_revision=stamp_revision,
        final_revision=final_revision,
        head_revision=head_revision,
    )
