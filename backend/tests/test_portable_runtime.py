"""Portable process identity and upgrade rollback primitives."""

from __future__ import annotations

import json
import socket
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


# --------------------------------------------------------------------------
# A port already in use must stop the launch, not adopt whoever holds it
# --------------------------------------------------------------------------


def _busy_port() -> tuple[socket.socket, int]:
    """Hold a real loopback port for the duration of a test."""
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    return holder, holder.getsockname()[1]


def test_a_free_port_is_reported_free_and_a_held_one_is_not():
    holder, port = _busy_port()
    try:
        assert portable._port_available(port) is False
    finally:
        holder.close()
    # Once released the same port is usable again.
    assert portable._port_available(port) is True


def test_starting_on_a_busy_port_writes_no_pid_file_and_takes_no_backup(tmp_path, capsys):
    """The real failure: the launcher wrote its PID record, took an upgrade
    backup and started the browser thread *before* uvicorn tried to bind. When
    the bind failed it exited 3, and anything already serving that port looked
    like a successful start."""
    holder, port = _busy_port()
    database = tmp_path / "jobagent.db"
    database.write_bytes(b"existing")
    try:
        code = portable.main([
            "--data-dir", str(tmp_path), "--port", str(port), "--no-open",
        ])
    finally:
        holder.close()

    assert code != 0, "a busy port must not report success"
    assert not (tmp_path / "runtime-process.json").exists(), "no PID record for a launch that never ran"
    assert not (tmp_path / "runtime-version.json").exists(), "never confirm a version we did not serve"
    assert database.read_bytes() == b"existing", "the database must be untouched"
    message = capsys.readouterr().out
    assert str(port) in message, "the message must name the port"


def test_the_doctor_checks_the_port_it_was_given_not_a_cached_one(tmp_path, capsys):
    """`get_settings()` is lru_cached, so setting APP_PORT cannot reach an
    already-constructed Settings. The doctor once read `cfg.app_port` and so
    checked whichever port was cached. That only looked correct locally,
    because the developer happened to have something on 8000; CI, where 8000 is
    free, caught it."""
    holder, busy_port = _busy_port()
    try:
        busy = portable.main(
            ["--data-dir", str(tmp_path), "--port", str(busy_port), "--doctor"]
        )
        busy_output = capsys.readouterr().out
    finally:
        holder.close()

    assert "FAIL port_available" in busy_output
    assert busy != 0, "doctor must fail while the port it would use is taken"

    # The same run on a port nothing holds must pass, proving the check follows
    # the argument rather than a cached or default value.
    free_holder, free_port = _busy_port()
    free_holder.close()
    ok = portable.main(["--data-dir", str(tmp_path), "--port", str(free_port), "--doctor"])
    free_output = capsys.readouterr().out
    assert "OK   port_available" in free_output
    assert ok == 0
