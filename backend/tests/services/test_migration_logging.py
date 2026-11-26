from __future__ import annotations

import logging
from pathlib import Path

from app.tms_core.services.bootstrap import run_migrations

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def test_run_migrations_keeps_startup_logging(tmp_path: Path) -> None:
    # 起動中の移行がログ設定を書き換えると、その後の起動ログと uvicorn の「起動完了」が消え、
    # 会社PCで「止まっている」と誤解された。既存のロガーと root の設定を変えないこと。
    app_logger = logging.getLogger("app.runtime")
    uvicorn_logger = logging.getLogger("uvicorn.error")
    # 先に走るテストが alembic コマンドを直接呼ぶと無効化されたまま残るので、起動時と同じ状態に戻す。
    app_logger.disabled = False
    uvicorn_logger.disabled = False
    root = logging.getLogger()
    root_level = root.level
    root_handlers = list(root.handlers)

    run_migrations(db_path=tmp_path / "migrated.db", project_root=BACKEND_ROOT)

    assert app_logger.disabled is False
    assert uvicorn_logger.disabled is False
    assert root.level == root_level
    assert root.handlers == root_handlers
