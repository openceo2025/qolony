from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import logging
import os

from app.iquavis.sync import IQuavisSyncService
from app.tms_core.config import Settings, load_settings
from app.tms_core.db.repository import TmsRepository
from app.tms_core.db.session import create_session_factory, create_sqlite_engine
from app.tms_core.services.bootstrap import bootstrap_data, run_migrations
from app.tms_core.services.colony import ColonyService
from app.tms_core.services.core import TmsService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Runtime:
    root_dir: Path
    settings: Settings
    service: TmsService
    repository: TmsRepository
    iquavis_sync: IQuavisSyncService
    colony: ColonyService


def settings_path(root_dir: Path) -> Path:
    """設定ファイルの場所。QOLONY_SETTINGS_PATH があればそれを使う。

    会社の利用者・パスワード・連携設定をアプリのフォルダーの外（運用データ側）に置き、
    GitHub から入れ替えたアプリで上書きされないようにするため。
    """
    configured = os.getenv("QOLONY_SETTINGS_PATH", "").strip()
    if configured:
        path = Path(configured)
        if not path.is_file():
            raise FileNotFoundError(f"QOLONY_SETTINGS_PATH が指す設定ファイルがありません: {path}")
        return path
    return root_dir / "config" / "settings.yaml"


def build_runtime(root_dir: Path) -> Runtime:
    logger.info("runtime init: load settings")
    settings = load_settings(path=settings_path(root_dir), config_dir=root_dir / "config")

    configured_db_path = Path(settings.app.db_path)
    if configured_db_path.is_absolute():
        db_path = configured_db_path
    else:
        db_path = (root_dir / configured_db_path).resolve()

    logger.info("runtime init: run migrations (%s)", db_path)
    run_migrations(db_path=db_path, project_root=root_dir)

    logger.info("runtime init: create repository")
    engine = create_sqlite_engine(db_path)
    session_factory = create_session_factory(engine)
    repository = TmsRepository(session_factory=session_factory)

    logger.info("runtime init: bootstrap data")
    bootstrap_data(settings=settings, repository=repository)

    logger.info("runtime init: create service")
    service = TmsService(settings=settings, repository=repository)
    try:
        import_result = service.import_member_link_targets()
        logger.info("runtime init: imported member link targets: %s", import_result)
    except Exception as exc:
        logger.info("runtime init: member link target import skipped: %s", exc)
    logger.info("runtime init: recompute task progress")
    service.recompute_all_task_progress()
    logger.info("runtime init: create iquavis sync service")
    iquavis_sync = IQuavisSyncService(
        repository=repository,
        tms_service=service,
    )
    logger.info("runtime init: complete")
    return Runtime(
        root_dir=root_dir,
        settings=settings,
        service=service,
        repository=repository,
        iquavis_sync=iquavis_sync,
        colony=ColonyService(repository=repository, tms=service),
    )
