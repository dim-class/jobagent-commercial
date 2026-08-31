"""Schema management.

Three cases, all handled without ever asking the developer to delete their
database:

===========================  ==================================================
brand-new database           ``create_all`` from the models, then stamp head
existing DB, never stamped   stamp the v0.3 baseline, then upgrade to head
already under Alembic        upgrade to head
===========================  ==================================================

The middle case is what makes a v0.3 database survive: its tables already match
revision ``0001_baseline``, so we record that fact instead of trying to create
tables that exist, and only the v0.4 columns are actually applied.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from app.core.config import get_settings
from app.core.logging import get_logger, log_event, setup_logging
from app.core.paths import BACKEND_DIR

logger = get_logger(__name__)

ALEMBIC_INI: Path = BACKEND_DIR / "alembic.ini"
BASELINE_REVISION = "0001_baseline"

#: A table that only ever exists in a database created by v0.1-v0.3.
_LEGACY_MARKER_TABLE = "jobs"


def alembic_config(engine: Engine) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    # ConfigParser treats "%" as interpolation, so escape it in the URL.
    cfg.set_main_option("sqlalchemy.url", str(engine.url).replace("%", "%%"))
    return cfg


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def has_legacy_tables(engine: Engine) -> bool:
    return _LEGACY_MARKER_TABLE in set(inspect(engine).get_table_names())


def _restore_app_logging() -> None:
    """Alembic's own ``env.py`` calls ``fileConfig(...)`` for every command
    below, which reconfigures the root logger's handlers/formatter from
    ``alembic.ini`` regardless of ``disable_existing_loggers`` - silently
    dropping this app's structured, redacted formatter (and the root level)
    back to a plain ``Formatter`` at ``WARNING``. Reapplied immediately after
    every Alembic invocation, before this module's own ``log_event`` calls,
    so a migration's own log lines - and everything logged for the rest of
    the process - keep their structured fields and redaction."""
    setup_logging(get_settings().log_level, force=True)


def ensure_schema_current(engine: Engine) -> str:
    """Bring the database to the latest revision. Returns what happened."""
    from app import models  # noqa: F401  - register mappers
    from app.db.base import Base

    revision = current_revision(engine)
    cfg = alembic_config(engine)

    if revision is not None:
        command.upgrade(cfg, "head")
        _restore_app_logging()
        log_event(logger, "db.migrated", frm=revision, to="head")
        return "upgraded"

    if has_legacy_tables(engine):
        # A v0.3 database: its tables already match the baseline, so record
        # that and let Alembic apply only the newer revisions.
        command.stamp(cfg, BASELINE_REVISION)
        command.upgrade(cfg, "head")
        _restore_app_logging()
        log_event(logger, "db.migrated_legacy", frm=BASELINE_REVISION, to="head")
        return "adopted_legacy"

    Base.metadata.create_all(bind=engine)
    command.stamp(cfg, "head")
    _restore_app_logging()
    log_event(logger, "db.created", tables=len(Base.metadata.tables))
    return "created"
