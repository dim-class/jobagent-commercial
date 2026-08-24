"""0010 -> 0011 schema upgrade: supervised_sessions + supervised_session_events (M4a).

Purely additive (no batch rebuild), checked for real: the new tables' shape,
their foreign keys and ondelete actions, that nothing existing changed
shape, and that a real ORM write round-trips through the upgraded file.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.migrations import current_revision, ensure_schema_current

from tests.test_migrations import columns, tables, upgrade_to, v03_db  # noqa: F401
from tests.test_migrations_orchestration_events import build_v11_database


def build_v12_database(path: Path) -> None:
    build_v11_database(path)
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    try:
        upgrade_to(engine, "0010_orchestration_events")
    finally:
        engine.dispose()


@pytest.fixture
def v12_db(tmp_path) -> Path:
    path = tmp_path / "v12.db"
    build_v12_database(path)
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
    "job_search_tasks",
    "task_candidates",
    "orchestration_events",
)


def test_the_upgrade_only_adds_the_supervised_session_tables(v12_db):
    before = {t: columns(v12_db, t) for t in TRACKED}
    assert upgrade(v12_db) == "upgraded"

    assert current_revision_of(v12_db) == "0011_supervised_sessions"
    assert {"supervised_sessions", "supervised_session_events"} <= set(tables(v12_db))
    for table, cols in before.items():
        assert columns(v12_db, table) == cols, f"{table} changed shape"


def test_supervised_session_ondelete_actions(v12_db):
    upgrade(v12_db)
    assert foreign_keys(v12_db, "supervised_sessions")["task_id"] == "CASCADE"
    assert foreign_keys(v12_db, "supervised_session_events")["session_id"] == "CASCADE"


def test_the_orm_can_write_and_query_the_upgraded_database(v12_db):
    from app.models import (
        Job,
        JobSearchTask,
        SupervisedSession,
        SupervisedSessionEvent,
        SupervisedSessionEventType,
        SupervisedSessionStatus,
    )

    upgrade(v12_db)
    engine = create_engine(f"sqlite:///{v12_db.as_posix()}")
    db = Session(bind=engine, future=True)
    try:
        job = db.scalars(select(Job)).first()
        assert job is not None

        task = JobSearchTask(name="升级后任务")
        db.add(task)
        db.commit()

        session = SupervisedSession(
            task_id=task.id,
            status=SupervisedSessionStatus.running,
            page_cap=3,
            candidate_cap=20,
            scroll_cap=5,
            tab_origin="https://www.zhipin.com",
        )
        db.add(session)
        db.commit()

        db.add(
            SupervisedSessionEvent(
                session_id=session.id, event_type=SupervisedSessionEventType.started
            )
        )
        db.commit()
        assert session.pages_visited == 0
        assert session.candidates_extracted == 0
        assert session.scrolls_used == 0

        db.delete(task)
        db.commit()
        assert db.scalars(select(SupervisedSession)).first() is None
        assert db.scalars(select(SupervisedSessionEvent)).first() is None
        # Deleting the task must never touch the job it referenced.
        assert db.get(Job, job.id) is not None
    finally:
        db.close()
        engine.dispose()


def test_a_v03_database_reaches_the_supervised_session_schema_in_one_go(v03_db):
    engine = create_engine(f"sqlite:///{v03_db.as_posix()}")
    try:
        assert ensure_schema_current(engine) == "adopted_legacy"
    finally:
        engine.dispose()

    assert current_revision_of(v03_db) == "0011_supervised_sessions"
    assert {"supervised_sessions", "supervised_session_events"} <= set(tables(v03_db))
