"""Developer CLI.

    python -m app.cli init-db          create data/jobagent.db and all tables
    python -m app.cli migrate          upgrade an existing database to head
    python -m app.cli seed             insert the demo jobs (idempotent)
    python -m app.cli seed --reset     delete demo jobs first, then re-insert
    python -m app.cli info             print non-secret configuration
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path

from app import __version__
from app.core.config import get_settings
from app.core.logging import ensure_utf8_stdout, setup_logging
from app.core.paths import DATA_DIR, ENV_FILE_PATH, ensure_runtime_dirs


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


def _port_is_free(host: str, port: int) -> bool:
    """Whether the configured port can be bound right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Check the local runtime without revealing configuration values."""
    cfg = get_settings()
    ensure_runtime_dirs()
    checks = {
        "data_directory": DATA_DIR.is_dir(),
        "data_directory_writable": DATA_DIR.is_dir() and os.access(DATA_DIR, os.W_OK),
        "strategy_file": cfg.strategy_file.is_file(),
        "database_parent": Path(cfg.sqlalchemy_url.removeprefix("sqlite:///")).parent.is_dir()
        if cfg.sqlalchemy_url.startswith("sqlite:///")
        else True,
        "frontend_bundle": (cfg.frontend_dist_path / "index.html").is_file()
        if cfg.serve_frontend
        else True,
        # The port the runtime would actually bind. A doctor that says PASS
        # while the port is taken sends the user to whatever else is serving
        # it - which is exactly how a failed start once looked healthy.
        "port_available": _port_is_free(cfg.app_host, cfg.app_port),
    }
    payload = {
        "ok": all(checks.values()),
        "version": __version__,
        "mode": "single_process" if cfg.serve_frontend else "development",
        "checks": checks,
        "openai_configured": cfg.openai_configured,
        "env_file_present": ENV_FILE_PATH.is_file(),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"JobAgent doctor: {'PASS' if payload['ok'] else 'FAIL'}")
        for name, passed in checks.items():
            print(f"  {'OK' if passed else 'FAIL':4} {name}")
        print(f"  INFO OpenAI configured: {'yes' if cfg.openai_configured else 'no (optional)'}")
    return 0 if payload["ok"] else 1


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
    doctor = sub.add_parser("doctor", help="check runtime files without printing secrets")
    doctor.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    doctor.set_defaults(func=_cmd_doctor)

    args = parser.parse_args(argv)
    ensure_utf8_stdout()
    setup_logging(get_settings().log_level)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
