from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from .models import Base



def build_sqlite_url(db_path: Path) -> str:
    resolved = db_path.resolve()
    return f"sqlite:///{resolved.as_posix()}"



def create_sqlite_engine(db_path: Path) -> Engine:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        build_sqlite_url(db_path),
        future=True,
        connect_args={"timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, _connection_record) -> None:
        # WAL: 書き込みを高速化し、読み取りが書き込みをブロックしない
        # (複数ユーザー同時アクセス時の "database is locked" も防ぐ)。
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    return engine



def create_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)



def create_all_tables(engine: Engine) -> None:
    Base.metadata.create_all(bind=engine)
