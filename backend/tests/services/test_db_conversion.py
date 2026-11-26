from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.tms_core.db.session import build_sqlite_url
from app.tms_core.services.db_conversion import (
    REVISION_0003,
    REVISION_0015,
    convert_legacy_db,
    read_alembic_revision,
)



def _backend_root() -> Path:
    return Path(__file__).resolve().parents[2]



def _build_alembic_config(db_path: Path) -> Config:
    backend_root = _backend_root()
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", build_sqlite_url(db_path))
    return cfg



def _upgrade_to_revision(db_path: Path, revision: str) -> None:
    cfg = _build_alembic_config(db_path)
    command.upgrade(cfg, revision)



def _table_columns(db_path: Path, table: str) -> set[str]:
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
    return {row[1] for row in rows}



def _has_table(db_path: Path, table: str) -> bool:
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
    return row is not None



def test_convert_legacy_db_from_0003_to_head(tmp_path) -> None:
    db_path = tmp_path / "legacy_0003.db"
    _upgrade_to_revision(db_path, REVISION_0003)

    result = convert_legacy_db(
        source_db_path=db_path,
        project_root=_backend_root(),
    )

    assert result.initial_revision == REVISION_0003
    assert result.final_revision == result.head_revision == REVISION_0015
    assert result.backup_path is not None
    assert result.backup_path.exists()

    assert _has_table(db_path, "RawWorklogs")
    assert _has_table(db_path, "DailyTaskActuals")
    assert _has_table(db_path, "DailyProgressSnapshots")
    assert _has_table(db_path, "IQuavisSyncJobs")
    assert _has_table(db_path, "MemberProfiles")
    assert _has_table(db_path, "MemberDayConfigs")
    assert "LeaveType" in _table_columns(db_path, "MemberDayConfigs")
    assert "Nickname" in _table_columns(db_path, "Users")
    assert "FullName" in _table_columns(db_path, "Users")
    assert "IsActive" in _table_columns(db_path, "Users")
    assert "ChildDependencyMode" in _table_columns(db_path, "Tasks")
    assert "WbsSortOrder" in _table_columns(db_path, "Tasks")
    assert "IQuavisTaskId" in _table_columns(db_path, "Tasks")
    assert "VAverage" in _table_columns(db_path, "DailyProgressSnapshots")



def test_convert_legacy_db_stamps_when_alembic_version_missing(tmp_path) -> None:
    db_path = tmp_path / "legacy_without_version.db"
    _upgrade_to_revision(db_path, REVISION_0003)

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("DROP TABLE alembic_version")
        conn.commit()

    result = convert_legacy_db(
        source_db_path=db_path,
        project_root=_backend_root(),
        make_backup=False,
    )

    assert result.initial_revision is None
    assert result.inferred_revision == REVISION_0003
    assert result.stamped_revision == REVISION_0003
    assert result.final_revision == REVISION_0015



def test_convert_legacy_db_to_output_keeps_source_intact(tmp_path) -> None:
    source_db = tmp_path / "legacy_source.db"
    target_db = tmp_path / "converted_target.db"
    _upgrade_to_revision(source_db, REVISION_0003)

    result = convert_legacy_db(
        source_db_path=source_db,
        target_db_path=target_db,
        project_root=_backend_root(),
        make_backup=False,
    )

    assert result.target_path == target_db.resolve()
    assert result.backup_path is None
    assert read_alembic_revision(source_db) == REVISION_0003
    assert read_alembic_revision(target_db) == REVISION_0015
