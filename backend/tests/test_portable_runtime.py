"""Portable process identity and upgrade rollback primitives."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import psutil

from app import portable


def write_json(path: Path, value: dict[str, object]) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_current_process_identity_requires_executable_and_creation_time():
    process = psutil.Process()
    record = {
        "pid": process.pid,
        "executable": process.exe(),
        "created_at": process.create_time(),
    }
    assert portable._same_process(record).pid == process.pid
    assert portable._same_process({**record, "created_at": process.create_time() - 60}) is None
    assert portable._same_process({**record, "executable": "C:/not-jobagent.exe"}) is None


def test_runtime_marks_ready_only_for_a_healthy_database():
    assert portable._is_healthy_payload({"status": "ok", "database": "ok"}) is True
    assert portable._is_healthy_payload({"status": "degraded", "database": "error"}) is False
    assert portable._is_healthy_payload({"status": "ok"}) is False
    assert portable._is_healthy_payload("ok") is False


def test_upgrade_backup_and_restore_preserve_database_and_strategy(tmp_path):
    database = tmp_path / "jobagent.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE proof(value TEXT NOT NULL)")
        connection.execute("INSERT INTO proof VALUES ('before')")
    strategy = tmp_path / "career_strategy.yaml"
    strategy.write_text("preferred_roles: [before]\n", encoding="utf-8")
    write_json(tmp_path / "runtime-version.json", {"version": "0.0.9"})

    backup = portable._backup_for_upgrade(tmp_path)
    assert backup is not None
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE proof SET value='after'")
    strategy.write_text("preferred_roles: [after]\n", encoding="utf-8")

    portable._restore_backup(tmp_path, backup)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM proof").fetchone()[0] == "before"
    assert strategy.read_text(encoding="utf-8") == "preferred_roles: [before]\n"


def test_same_version_does_not_create_redundant_upgrade_backup(tmp_path):
    (tmp_path / "jobagent.db").touch()
    write_json(tmp_path / "runtime-version.json", {"version": portable.__version__})
    assert portable._backup_for_upgrade(tmp_path) is None
    assert not (tmp_path / "backups").exists()


def test_import_phase_failure_uses_the_same_upgrade_rollback(tmp_path):
    database = tmp_path / "jobagent.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE proof(value TEXT NOT NULL)")
        connection.execute("INSERT INTO proof VALUES ('safe')")
    write_json(tmp_path / "runtime-version.json", {"version": "0.0.8"})
    backup = portable._backup_for_upgrade(tmp_path)
    assert backup is not None
    database.write_bytes(b"broken during import")

    portable._rollback_failed_start(tmp_path, backup)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM proof").fetchone()[0] == "safe"
