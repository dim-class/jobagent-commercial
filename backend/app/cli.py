"""Developer CLI.

    python -m app.cli init-db          create data/jobagent.db and all tables
    python -m app.cli migrate          upgrade an existing database to head
    python -m app.cli seed             insert the demo jobs (idempotent)
    python -m app.cli seed --reset     delete demo jobs first, then re-insert
    python -m app.cli info             print non-secret configuration
"""

from __future__ import annotations

import argparse
import sys

from app import __version__
from app.core.config import get_settings
from app.core.logging import ensure_utf8_stdout, setup_logging


def _cmd_init_db(_: argparse.Namespace) -> int:
    from app.db.session import init_db

    init_db()
    print(f"OK  database initialised at {get_settings().database_url}")
    return 0


def _cmd_migrate(_: argparse.Namespace) -> int:
    from app.db.migrations import current_revision, ensure_schema_current
    from app.db.session import engine

    action = ensure_schema_current(engine)
    print(f"OK  schema {action}; now at revision {current_revision(engine)}")
    return 0


def _cmd_seed(args: argparse.Namespace) -> int:
    from app.db.session import init_db, session_scope
    from app.services.seed import seed_demo_jobs

    init_db()
    with session_scope() as db:
        stats = seed_demo_jobs(db, reset=args.reset)
    print(
        f"OK  demo jobs seeded: created={stats['created']} "
        f"skipped(existing)={stats['skipped']} total={stats['total']}"
    )
    print("    NOTE: demo jobs are fictional and marked [演示数据]; they are not real vacancies.")
    return 0


def _cmd_info(_: argparse.Namespace) -> int:
    cfg = get_settings()
    print(f"AI Job Agent backend v{__version__}")
    for key, value in cfg.safe_dump().items():
        print(f"  {key:22} {value}")
    print(f"  {'strategy_file':22} {cfg.strategy_file}")
    if not cfg.openai_configured:
        print("\n  OPENAI_API_KEY is NOT set - AI endpoints will return a 503 config error.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="AI Job Agent dev CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="create the SQLite database and tables").set_defaults(
        func=_cmd_init_db
    )

    seed = sub.add_parser("seed", help="insert demo jobs")
    seed.add_argument("--reset", action="store_true", help="delete existing demo jobs first")
    seed.set_defaults(func=_cmd_seed)

    sub.add_parser(
        "migrate", help="create or upgrade the database schema (alembic upgrade head)"
    ).set_defaults(func=_cmd_migrate)

    sub.add_parser("info", help="print non-secret configuration").set_defaults(func=_cmd_info)

    args = parser.parse_args(argv)
    ensure_utf8_stdout()
    setup_logging(get_settings().log_level)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
