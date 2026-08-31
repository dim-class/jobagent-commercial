"""Single-process entry point for the Windows portable release.

Build-time tooling supplies the compiled frontend and bundles Python. Runtime
data remains outside the bundle under the per-user data root selected by
``app.core.paths``. This module never starts a recruitment task or browser.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import threading
import time
import urllib.request
import webbrowser
from datetime import UTC, datetime
from pathlib import Path

import psutil

from app import __version__


def _runtime_data_dir(override: str | None = None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    configured = os.environ.get("JOBAGENT_DATA_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA", "").strip()
        return (Path(base) if base else Path.home() / "AppData" / "Local") / "JobAgent"
    return Path(__file__).resolve().parents[2] / "data"


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _same_process(record: dict[str, object]) -> psutil.Process | None:
    try:
        pid = int(record["pid"])
        expected_executable = os.path.normcase(os.path.abspath(str(record["executable"])))
        expected_created = float(record["created_at"])
        process = psutil.Process(pid)
        actual_executable = os.path.normcase(os.path.abspath(process.exe()))
        if actual_executable != expected_executable:
            return None
        if abs(process.create_time() - expected_created) > 0.01:
            return None
        return process
    except (KeyError, TypeError, ValueError, OSError, psutil.Error):
        return None


def _stop_existing(pid_file: Path) -> int:
    record = _read_json(pid_file)
    process = _same_process(record or {})
    if process is None:
        pid_file.unlink(missing_ok=True)
        print("JobAgent is not running.")
        return 0
    process.terminate()
    try:
        process.wait(timeout=10)
    except psutil.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    pid_file.unlink(missing_ok=True)
    print("JobAgent stopped.")
    return 0


def _backup_for_upgrade(data_dir: Path) -> Path | None:
    database = data_dir / "jobagent.db"
    marker = data_dir / "runtime-version.json"
    installed = _read_json(marker) or {}
    if not database.is_file() or installed.get("version") == __version__:
        return None

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    previous = str(installed.get("version") or "unknown").replace("/", "_").replace("\\", "_")
    backup = data_dir / "backups" / f"pre-{__version__}-from-{previous}-{timestamp}"
    backup.mkdir(parents=True, exist_ok=False)
    with sqlite3.connect(database) as source, sqlite3.connect(backup / "jobagent.db") as target:
        source.backup(target)
    strategy = data_dir / "career_strategy.yaml"
    if strategy.is_file():
        shutil.copy2(strategy, backup / strategy.name)
    _atomic_json(backup / "backup.json", {
        "from_version": installed.get("version"),
        "to_version": __version__,
        "strategy_existed": strategy.is_file(),
    })
    return backup


def _restore_backup(data_dir: Path, backup: Path) -> None:
    # Dispose SQLAlchemy before this function is called. SQLite sidecars from a
    # failed startup must not survive beside the restored, consistent backup.
    for suffix in ("-wal", "-shm", "-journal"):
        (data_dir / f"jobagent.db{suffix}").unlink(missing_ok=True)
    shutil.copy2(backup / "jobagent.db", data_dir / "jobagent.db")
    metadata = _read_json(backup / "backup.json") or {}
    strategy = data_dir / "career_strategy.yaml"
    saved_strategy = backup / strategy.name
    if saved_strategy.is_file():
        shutil.copy2(saved_strategy, strategy)
    elif metadata.get("strategy_existed") is False:
        strategy.unlink(missing_ok=True)


def _rollback_failed_start(data_dir: Path, backup: Path | None) -> None:
    if backup is None:
        return
    try:
        from app.db.session import engine

        engine.dispose()
    except ImportError:
        pass
    _restore_backup(data_dir, backup)


def _is_healthy_payload(payload: object) -> bool:
    return (
        isinstance(payload, dict)
        and payload.get("status") == "ok"
        and payload.get("database") == "ok"
    )


def _open_when_ready(url: str, data_dir: Path, ready: threading.Event, no_open: bool) -> None:
    for _ in range(80):
        try:
            with urllib.request.urlopen(f"{url}health", timeout=1) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if response.status == 200 and _is_healthy_payload(payload):
                    _atomic_json(data_dir / "runtime-version.json", {
                        "version": __version__,
                        "confirmed_at": datetime.now(UTC).isoformat(),
                    })
                    ready.set()
                    if not no_open:
                        webbrowser.open(f"{url}#/setup")
                    return
        except (OSError, UnicodeError, ValueError):
            time.sleep(0.25)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="JobAgent", description="JobAgent Windows runtime")
    parser.add_argument("--data-dir", help="override the per-user runtime directory")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--doctor", action="store_true")
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535")

    data_dir = _runtime_data_dir(args.data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["JOBAGENT_DATA_DIR"] = str(data_dir)
    os.environ.setdefault("JOBAGENT_SERVE_FRONTEND", "true")
    os.environ.setdefault("APP_HOST", "127.0.0.1")
    os.environ["APP_PORT"] = str(args.port)

    pid_file = data_dir / "runtime-process.json"
    if args.stop:
        return _stop_existing(pid_file)
    if args.doctor:
        from app.cli import main as cli_main

        return cli_main(["doctor"])

    existing_record = _read_json(pid_file) or {}
    existing = _same_process(existing_record)
    url = f"http://127.0.0.1:{args.port}/"
    if existing is not None:
        recorded_port = existing_record.get("port")
        if isinstance(recorded_port, int) and 1024 <= recorded_port <= 65535:
            url = f"http://127.0.0.1:{recorded_port}/"
        if not args.no_open:
            webbrowser.open(f"{url}#/setup")
        print("JobAgent is already running.")
        return 0

    pid_file.unlink(missing_ok=True)
    backup = _backup_for_upgrade(data_dir)

    # Import only after packaged-mode defaults are fixed in the environment.
    # Import-time failures are startup failures too; an upgrade backup must be
    # restored even when FastAPI never reaches its lifespan.
    try:
        import uvicorn
        from app.main import app
    except Exception:
        _rollback_failed_start(data_dir, backup)
        raise

    process = psutil.Process()
    _atomic_json(pid_file, {
        "pid": process.pid,
        "executable": process.exe(),
        "created_at": process.create_time(),
        "port": args.port,
        "version": __version__,
    })
    ready = threading.Event()
    threading.Thread(
        target=_open_when_ready,
        args=(url, data_dir, ready, args.no_open),
        daemon=True,
    ).start()
    try:
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
    finally:
        current = _read_json(pid_file)
        if current and int(current.get("pid", -1)) == process.pid:
            pid_file.unlink(missing_ok=True)
        if backup is not None and not ready.is_set():
            _rollback_failed_start(data_dir, backup)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
