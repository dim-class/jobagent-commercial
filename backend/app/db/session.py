"""SQLite engine / session factory.

SQLite specifics we care about locally:
  * ``check_same_thread=False`` because FastAPI runs sync endpoints in a
    threadpool;
  * WAL journal so the dashboard can read while an analysis writes;
  * ``PRAGMA foreign_keys=ON`` which SQLite otherwise leaves off.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.logging import get_logger, log_event
from app.core.paths import ensure_runtime_dirs

logger = get_logger(__name__)

_is_sqlite = settings.sqlalchemy_url.startswith("sqlite")

engine: Engine = create_engine(
    settings.sqlalchemy_url,
    echo=False,
    future=True,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, connection_record) -> None:  # noqa: ANN001
    if not _is_sqlite:
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for scripts and services."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """Create or migrate the database (idempotent).

    Since v0.4 the schema is owned by Alembic. An existing v0.3 database is
    adopted and upgraded in place - deleting it is never required.
    """
    ensure_runtime_dirs()
    from app.db.base import Base
    from app import models  # noqa: F401  (registers mappers)
    from app.db.migrations import ensure_schema_current

    action = ensure_schema_current(engine)
    log_event(
        logger,
        "db.initialized",
        url=settings.database_url,
        tables=len(Base.metadata.tables),
        action=action,
    )
