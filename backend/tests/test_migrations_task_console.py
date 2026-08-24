"""0008 -> 0009 schema upgrade: job_search_tasks + task_candidates.

Purely additive (no batch rebuild), but still checked for real: the new
tables' shape, their foreign keys and ondelete actions, the
UNIQUE(task_id, job_id) constraint, that nothing existing changed shape, and
that a real ORM write round-trips through the upgraded file.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.migrations import current_revision, ensure_schema_current

from tests.test_migrations import columns, tables, upgrade_to, v03_db  # noqa: F401
from tests.test_migrations_v10 import build_v09_database


def build_v10_database(path: Path) -> None:
    build_v09_database(path)
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    try:
        upgrade_to(engine, "0008_decision_support")
    finally:
        engine.dispose()


@pytest.fixture
def v10_db(tmp_path) -> Path:
    path = tmp_path / "v10.db"
    build_v10_database(path)
    return path


def upgrade(path: Path) -> str:
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    try:
        return ensure_schema_current(engine)
    finally:
        engine.dispose()


def current_revision_of(path: Path) -> str:
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    try:
        return current_revision(engine)
    finally:
        engine.dispose()


def foreign_keys(path: Path, table: str) -> dict[str, str]:
    con = sqlite3.connect(path)
    try:
        return {row[3]: row[6] for row in con.execute(f"pragma foreign_key_list({table})")}
    finally:
        con.close()


TRACKED = (
    "jobs",
    "job_analyses",
    "resumes",
    "decision_profiles",
    "offer_assessments",
    "decision_snapshots",
)


def test_the_upgrade_only_adds_the_task_console_tables(v10_db):
    before = {t: columns(v10_db, t) for t in TRACKED}
    assert upgrade(v10_db) == "upgraded"

    assert current_revision_of(v10_db) == "0011_supervised_sessions"
    assert {"job_search_tasks", "task_candidates"} <= set(tables(v10_db))
    for table, cols in before.items():
        assert columns(v10_db, table) == cols, f"{table} changed shape"


def test_task_candidates_ondelete_actions(v10_db):
    upgrade(v10_db)
    keys = foreign_keys(v10_db, "task_candidates")
    assert keys["task_id"] == "CASCADE"
    assert keys["job_id"] == "CASCADE"
    assert foreign_keys(v10_db, "job_search_tasks")["resume_id"] == "SET NULL"


def test_task_candidates_unique_constraint_is_enforced(v10_db):
    upgrade(v10_db)
    con = sqlite3.connect(v10_db)
    try:
        con.execute(
            "INSERT INTO job_search_tasks (name, mode, created_at, updated_at)"
            " VALUES ('t', 'manual_review_only', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        task_id = con.execute("select id from job_search_tasks").fetchone()[0]
        con.execute(
            "INSERT INTO task_candidates (task_id, job_id, created_at, updated_at)"
            " VALUES (?, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (task_id,),
        )
        con.commit()
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO task_candidates (task_id, job_id, created_at, updated_at)"
                " VALUES (?, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                (task_id,),
            )
    finally:
        con.close()


def test_the_orm_can_write_and_query_the_upgraded_database(v10_db):
    from app.models import Job, JobSearchTask, TaskCandidate

    upgrade(v10_db)
    engine = create_engine(f"sqlite:///{v10_db.as_posix()}")
    db = Session(bind=engine, future=True)
    try:
        job = db.scalars(select(Job)).first()
        assert job is not None

        task = JobSearchTask(name="升级后任务")
        db.add(task)
        db.commit()

        assoc = TaskCandidate(task_id=task.id, job_id=job.id)
        db.add(assoc)
        db.commit()

        dup = TaskCandidate(task_id=task.id, job_id=job.id)
        db.add(dup)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        db.delete(task)
        db.commit()
        assert db.scalars(select(TaskCandidate)).first() is None
        # Deleting the task must never touch the job it referenced.
        assert db.get(Job, job.id) is not None
    finally:
        db.close()
        engine.dispose()


def test_a_v03_database_reaches_the_task_console_schema_in_one_go(v03_db):
    engine = create_engine(f"sqlite:///{v03_db.as_posix()}")
    try:
        assert ensure_schema_current(engine) == "adopted_legacy"
    finally:
        engine.dispose()

    assert current_revision_of(v03_db) == "0011_supervised_sessions"
    assert {"job_search_tasks", "task_candidates"} <= set(tables(v03_db))
