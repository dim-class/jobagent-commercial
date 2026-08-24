"""v0.6 -> v0.7 schema upgrade.

Split from ``test_migrations.py`` only for length; it reuses that module's
fixtures and helpers so there is exactly one definition of what each historical
schema looked like.

The regression that matters here is the SQLite cascade: rebuilding ``resumes``
in batch mode drops the original table, which fires the ON DELETE CASCADE from
``job_analyses.resume_id`` and would wipe every stored analysis.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.db.migrations import ensure_schema_current

from tests.test_migrations import (
    build_v05_database,
    columns,
    stamp,
    upgrade_to,
    tables,
    v03_db,  # noqa: F401 - re-exported fixture
)

# A v0.6 database: everything v0.6 had, and no resume-variant columns.
V06_EXTRA = """
CREATE TABLE career_strategy_changes (
    id INTEGER NOT NULL PRIMARY KEY,
    before_hash VARCHAR(64) NOT NULL,
    after_hash VARCHAR(64) NOT NULL,
    before_json JSON NOT NULL,
    after_json JSON NOT NULL,
    source VARCHAR(32) NOT NULL,
    recommendation_signature VARCHAR(128),
    notes TEXT,
    created_at DATETIME NOT NULL
);
CREATE TABLE strategy_recommendation_decisions (
    id INTEGER NOT NULL PRIMARY KEY,
    signature VARCHAR(128) NOT NULL,
    decision VARCHAR(16) NOT NULL,
    notes TEXT,
    created_at DATETIME NOT NULL,
    CONSTRAINT uq_strategy_recommendation_signature UNIQUE (signature)
);
"""


def build_v06_database(path: Path) -> None:
    build_v05_database(path)
    con = sqlite3.connect(path)
    try:
        con.executescript(V06_EXTRA)
        con.execute(
            "INSERT INTO career_strategy_changes (before_hash, after_hash, before_json,"
            " after_json, source, notes, created_at)"
            " VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP)",
            ("a" * 64, "b" * 64, "{}", "{}", "analytics_recommendation", "v0.6 的策略调整"),
        )
        con.commit()
    finally:
        con.close()


@pytest.fixture
def v06_db(tmp_path) -> Path:
    path = tmp_path / "v06.db"
    build_v06_database(path)
    return path


def upgrade_from_v06(engine) -> str:
    stamp(engine, "0004_strategy_analytics")
    return ensure_schema_current(engine)


# --------------------------------------------------------------------------


def test_a_v06_database_has_no_resume_variant_columns(v06_db):
    """Guards the fixture: it must really represent the older schema."""
    assert "career_strategy_changes" in tables(v06_db)
    assert "variant_name" not in columns(v06_db, "resumes")
    assert "resume_id" not in columns(v06_db, "application_events")


def test_v06_database_upgrades_and_keeps_every_row(v06_db):
    engine = create_engine(f"sqlite:///{v06_db.as_posix()}")
    try:
        assert upgrade_from_v06(engine) == "upgraded"

        with engine.connect() as conn:
            assert conn.execute(text("select count(*) from jobs")).scalar() == 1
            assert conn.execute(text("select count(*) from resumes")).scalar() == 1
            assert conn.execute(text("select count(*) from application_events")).scalar() == 3
            assert conn.execute(
                text("select count(*) from recruiter_conversations")
            ).scalar() == 1
            assert conn.execute(
                text("select count(*) from career_strategy_changes")
            ).scalar() == 1
    finally:
        engine.dispose()


def test_the_v07_migration_does_not_cascade_away_job_analyses(v06_db):
    """Regression guard.

    SQLite cannot ALTER TABLE ADD CONSTRAINT, so Alembic's batch mode rebuilds
    the table by dropping the original - and that DROP fires the ON DELETE
    CASCADE from job_analyses.resume_id. Foreign keys are enabled in this app
    (db/session.py), so rebuilding ``resumes`` would silently delete every
    stored analysis. The migration suspends foreign keys for exactly this.
    """
    engine = create_engine(f"sqlite:///{v06_db.as_posix()}")
    try:
        with engine.connect() as conn:
            before = conn.execute(text("select count(*) from job_analyses")).scalar()
        assert before == 1, "the fixture really has an analysis to lose"

        upgrade_from_v06(engine)

        with engine.connect() as conn:
            after = conn.execute(text("select count(*) from job_analyses")).scalar()
            cache_key = conn.execute(text("select cache_key from job_analyses")).scalar()
    finally:
        engine.dispose()

    assert after == 1, "the resumes rebuild must not cascade into job_analyses"
    assert cache_key == "c" * 64, "and it is the original row, not a replacement"


def test_the_v07_migration_leaves_no_foreign_key_violations(v06_db):
    engine = create_engine(f"sqlite:///{v06_db.as_posix()}")
    try:
        upgrade_from_v06(engine)
        with engine.connect() as conn:
            violations = conn.execute(text("pragma foreign_key_check")).fetchall()
    finally:
        engine.dispose()
    assert violations == []


def test_historical_applications_are_never_backfilled(v06_db):
    """The rule that matters: an old application's resume stays unknown.

    Guessing it from whichever resume is active today would invent history.
    """
    engine = create_engine(f"sqlite:///{v06_db.as_posix()}")
    try:
        upgrade_from_v06(engine)
        with engine.connect() as conn:
            rows = conn.execute(
                text("select resume_id from application_events where event_type='applied'")
            ).fetchall()
            active = conn.execute(text("select id from resumes where is_active=1")).scalar()
    finally:
        engine.dispose()

    assert rows, "the fixture has a v0.4-era applied event"
    assert all(row[0] is None for row in rows), "no attribution was invented"
    assert active is not None, "and an active resume existed that could have been guessed"


def test_existing_resumes_get_a_starting_variant_name(v06_db):
    """A label, not an attribution - it says nothing about past applications."""
    engine = create_engine(f"sqlite:///{v06_db.as_posix()}")
    try:
        upgrade_from_v06(engine)
        with engine.connect() as conn:
            name, filename = conn.execute(
                text("select variant_name, filename from resumes where id=1")
            ).one()
    finally:
        engine.dispose()
    assert name == filename


def test_the_v07_migration_is_purely_additive(v06_db):
    engine = create_engine(f"sqlite:///{v06_db.as_posix()}")
    try:
        stamp(engine, "0004_strategy_analytics")
        before = {t: columns(v06_db, t) for t in tables(v06_db)}
        # Upgrade to 0005 exactly - not to head, which now includes v0.8.
        upgrade_to(engine, "0005_resume_variants")
    finally:
        engine.dispose()

    after = {t: columns(v06_db, t) for t in tables(v06_db)}
    assert set(after) == set(before), "v0.7 adds no tables"

    added = {t: after[t] - before[t] for t in before if after[t] != before[t]}
    assert added == {
        "resumes": {
            "variant_name",
            "variant_group",
            "parent_resume_id",
            "notes",
            "archived_at",
        },
        "application_events": {"resume_id"},
    }


def test_resume_upgrade_is_idempotent(v06_db):
    engine = create_engine(f"sqlite:///{v06_db.as_posix()}")
    try:
        assert upgrade_from_v06(engine) == "upgraded"
        assert ensure_schema_current(engine) == "upgraded"
        with engine.connect() as conn:
            assert conn.execute(text("select count(*) from job_analyses")).scalar() == 1
    finally:
        engine.dispose()


def test_a_v03_database_reaches_the_resume_schema_in_one_go(v03_db):
    """The oldest supported database still upgrades all the way to v0.7 head."""
    engine = create_engine(f"sqlite:///{v03_db.as_posix()}")
    try:
        assert ensure_schema_current(engine) == "adopted_legacy"
        with engine.connect() as conn:
            assert conn.execute(text("select count(*) from jobs")).scalar() == 1
            assert conn.execute(text("select count(*) from job_analyses")).scalar() == 1
    finally:
        engine.dispose()
    assert "variant_name" in columns(v03_db, "resumes")
    assert "resume_id" in columns(v03_db, "application_events")
