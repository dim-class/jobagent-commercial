"""Alembic environment.

The URL comes from ``app.core.config`` rather than alembic.ini, so migrations
target exactly the database the app uses (including the repo-root-relative
sqlite path resolution).
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.db.base import Base
from app import models  # noqa: F401  - registers every mapper on Base.metadata

config = context.config
if config.config_file_name is not None:
    # disable_existing_loggers defaults to True, which switches off every
    # logger already created - including the whole `app.*` tree. Since
    # `init_db()` runs migrations during startup, that silently killed all
    # application logging for the rest of the process.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# A caller (app.db.migrations) may hand us an explicit URL - for example when
# upgrading a temporary or fixture database. Only fall back to the application
# settings when nothing was provided, otherwise we would silently migrate the
# wrong database.
if not config.get_main_option("sqlalchemy.url", None):
    config.set_main_option(
        "sqlalchemy.url", get_settings().sqlalchemy_url.replace("%", "%%")
    )

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite cannot ALTER most things in place; batch mode rewrites
            # the table instead of failing.
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
