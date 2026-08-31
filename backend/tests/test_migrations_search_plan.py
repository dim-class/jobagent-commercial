"""0011 -> 0012 schema upgrade: SearchPlan/runner columns on job_search_tasks
(M4e/M4f).

Purely additive `ADD COLUMN`s (no batch rebuild), checked for real: the
columns exist with the right defaults, every pre-existing row (created
before this migration ran) reads back with those defaults rather than an
error or a NULL where a NOT NULL default was promised, and a real ORM
write/read round-trips through the upgraded file.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.migrations import current_revision, ensure_schema_current

from tests.test_migrations import columns, upgrade_to, v03_db  # noqa: F401
from tests.test_migrations_supervised_sessions import build_v12_database

NEW_COLUMNS = frozenset(
    {
        # 0021 snapshots the candidate-stage policy onto every task.
        "early_career_policy",
        "city_id",
        "is_search_plan",
        "run_status",
        "run_started_at",
        "run_stopped_at",
        "observed_count",
        "new_count",
        "duplicate_count",
        "no_new_rounds",
        "last_error",
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


def test_the_upgrade_adds_exactly_the_search_plan_columns(v12_db):
    before = columns(v12_db, "job_search_tasks")
    assert upgrade(v12_db) == "upgraded"
    assert current_revision_of(v12_db) == "0021_candidate_stage_policy"

    after = columns(v12_db, "job_search_tasks")
    assert after - before == NEW_COLUMNS
    assert before <= after


def test_a_pre_existing_task_row_gets_the_documented_defaults(v12_db):
    """A task created *before* 0012 ran must read back with the new
    columns' server defaults, never a migration error and never a bare NULL
    where a NOT NULL default (e.g. `is_search_plan`, every counter) was
    promised."""
    engine = create_engine(f"sqlite:///{v12_db.as_posix()}")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO job_search_tasks (name, mode, created_at, updated_at) "
                "VALUES ('迁移前任务', 'manual_review_only', datetime('now'), datetime('now'))"
            )
    finally:
        engine.dispose()

    upgrade(v12_db)

    from app.models import JobSearchTask

    engine = create_engine(f"sqlite:///{v12_db.as_posix()}")
    db = Session(bind=engine, future=True)
    try:
        task = db.scalars(select(JobSearchTask).where(JobSearchTask.name == "迁移前任务")).one()
        assert task.city_id is None
        assert task.is_search_plan is False
        assert task.run_status is None
        assert task.run_started_at is None
        assert task.observed_count == 0
        assert task.new_count == 0
        assert task.duplicate_count == 0
        assert task.no_new_rounds == 0
        assert task.last_error is None
        assert task.match_run_json is None
        assert task.match_revision == 0
    finally:
        db.close()
        engine.dispose()


def test_the_orm_can_write_and_read_a_search_plan_task_after_upgrade(v12_db):
    from app.models import JobSearchTask, SearchTaskRunStatus, TaskMode

    upgrade(v12_db)
    engine = create_engine(f"sqlite:///{v12_db.as_posix()}")
    db = Session(bind=engine, future=True)
    try:
        task = JobSearchTask(
            name="北京 · SRE",
            keywords="SRE",
            city="北京",
            city_id="101010100",
            is_search_plan=True,
            run_status=SearchTaskRunStatus.pending,
            mode=TaskMode.manual_review_only,
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        reread = db.get(JobSearchTask, task.id)
        assert reread is not None
        assert reread.city_id == "101010100"
        assert reread.is_search_plan is True
        assert reread.run_status == SearchTaskRunStatus.pending
        assert reread.observed_count == 0
    finally:
        db.close()
        engine.dispose()


def test_a_v03_database_reaches_the_search_plan_schema_in_one_go(v03_db):
    engine = create_engine(f"sqlite:///{v03_db.as_posix()}")
    try:
        assert ensure_schema_current(engine) == "adopted_legacy"
    finally:
        engine.dispose()

    assert current_revision_of(v03_db) == "0021_candidate_stage_policy"
    assert NEW_COLUMNS <= columns(v03_db, "job_search_tasks")
