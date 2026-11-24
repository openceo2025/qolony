from __future__ import annotations

import logging
from pathlib import Path

import flet as ft

from app.tms_core.config import load_settings
from app.tms_core.db.repository import TmsRepository
from app.tms_core.db.session import create_session_factory, create_sqlite_engine
from app.tms_core.services.bootstrap import bootstrap_data, run_migrations
from app.tms_core.services.core import TmsService
from app.tms_core.ui.app import build_app



def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )



def run() -> None:
    _setup_logging()

    settings = load_settings()

    project_root = Path(__file__).resolve().parents[2]
    configured_db_path = Path(settings.app.db_path)
    if configured_db_path.is_absolute():
        db_path = configured_db_path
    else:
        db_path = (project_root / configured_db_path).resolve()

    run_migrations(db_path=db_path, project_root=project_root)

    engine = create_sqlite_engine(db_path)
    session_factory = create_session_factory(engine)
    repository = TmsRepository(session_factory=session_factory)

    bootstrap_data(settings=settings, repository=repository)

    service = TmsService(settings=settings, repository=repository)
    service.recompute_all_task_progress()

    ft.run(main=lambda page: build_app(page, service, settings), view=ft.AppView.FLET_APP)


if __name__ == "__main__":
    run()
