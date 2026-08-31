"""0009 -> 0010 schema upgrade: orchestration_events (M3).

Purely additive (no batch rebuild), checked for real: the new table's shape,
its foreign keys and ondelete actions, that nothing existing changed shape,
and that a real ORM write round-trips through the upgraded file.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.migrations import current_revision, ensure_schema_current

from tests.test_migrations import columns, tables, upgrade_to, v03_db  # noqa: F401
from tests.test_migrations_task_console import build_v10_database


def build_v11_database(path: Path) -> None:
    build_v10_database(path)
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    try:
        upgrade_to(engine, "0009_job_search_tasks")
    finally:
        engine.dispose()


@pytest.fixture
def v11_db(tmp_path) -> Path:
    path = tmp_path / "v11.db"
    build_v11_database(path)
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
)

#: 0012 (M4e/M4f, later than the 0010 revision this file is actually about)
#: adds exactly these columns to `job_search_tasks` - additive only, never a
#: shape change to any column this file's own migration owns. Every *other*
#: tracked table must still be byte-for-byte identical, the original,
#: stronger assertion this test already made.
JOB_SEARCH_TASKS_0012_COLUMNS = frozenset(
    {
        
        # 0021 snapshots the candidate-stage policy onto every task.
        "early_career_policy","city_id",
        "is_search_plan",
        "run_status",
        "run_started_at",
        "run_stopped_at",
        "observed_count",
        "new_count",
        "duplicate_count",
        "no_new_rounds",
        "last_error",
        # 0013: runner observability
        "current_url",
        "scroll_round",
        "visible_jobs",
        "imported_jobs",
        "current_candidate",
        "last_action",
        "paused_reason",
        "match_run_json",
        "match_revision",
    }
)


def test_the_upgrade_only_adds_the_orchestration_events_table(v11_db):
    before = {t: columns(v11_db, t) for t in TRACKED}
    assert upgrade(v11_db) == "upgraded"

    assert current_revision_of(v11_db) == "0021_candidate_stage_policy"
    assert "orchestration_events" in set(tables(v11_db))
    for table, cols in before.items():
        after = columns(v11_db, table)
        if table == "job_search_tasks":
            assert cols <= after, f"{table} lost a column"
            assert after - cols == JOB_SEARCH_TASKS_0012_COLUMNS, (
                f"{table} gained unexpected columns: {after - cols - JOB_SEARCH_TASKS_0012_COLUMNS}"
            )
        else:
            expected = cols | ({"source_message_id"} if table == "recruiter_messages" else set())
            assert after == expected, f"{table} changed shape"


def test_orchestration_events_ondelete_actions(v11_db):
    upgrade(v11_db)
    keys = foreign_keys(v11_db, "orchestration_events")
    assert keys["task_id"] == "CASCADE"
    assert keys["job_id"] == "CASCADE"


def test_the_orm_can_write_and_query_the_upgraded_database(v11_db):
    from app.models import Job, JobSearchTask, OrchestrationEvent, OrchestrationEventType

    upgrade(v11_db)
    engine = create_engine(f"sqlite:///{v11_db.as_posix()}")
    db = Session(bind=engine, future=True)
    try:
        job = db.scalars(select(Job)).first()
        assert job is not None

        task = JobSearchTask(name="升级后任务")
        db.add(task)
        db.commit()

        event = OrchestrationEvent(
            task_id=task.id, job_id=job.id, event_type=OrchestrationEventType.opened
        )
        db.add(event)
        db.commit()

        task_level = OrchestrationEvent(task_id=task.id, event_type=OrchestrationEventType.reviewed)
        db.add(task_level)
        db.commit()
        assert task_level.job_id is None

        db.delete(task)
        db.commit()
        assert db.scalars(select(OrchestrationEvent)).first() is None
        # Deleting the task must never touch the job it referenced.
        assert db.get(Job, job.id) is not None
    finally:
        db.close()
        engine.dispose()


def test_a_v03_database_reaches_the_orchestration_events_schema_in_one_go(v03_db):
    engine = create_engine(f"sqlite:///{v03_db.as_posix()}")
    try:
        assert ensure_schema_current(engine) == "adopted_legacy"
    finally:
        engine.dispose()

    assert current_revision_of(v03_db) == "0021_candidate_stage_policy"
    assert "orchestration_events" in set(tables(v03_db))
