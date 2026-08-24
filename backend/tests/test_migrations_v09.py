"""v0.8 -> v0.9 schema upgrade.

Two migration bugs have shipped in this repo before: a SQLite batch rebuild
that cascaded away every ``job_analyses`` row (v0.7), and a missing
``server_default`` that made an upgraded schema reject every insert (v0.8,
briefly). This module tests for both classes explicitly - including a real
insert against the migrated schema, not just a column-presence check.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.db.migrations import current_revision, ensure_schema_current

from tests.test_migrations import columns, stamp, tables, upgrade_to, v03_db  # noqa: F401
from tests.test_migrations_v08 import build_v07_database

#: A v0.8 database: v0.7 plus the interview pipeline.
V08_EXTRA = """
CREATE TABLE interview_processes (
    id INTEGER NOT NULL PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    applied_event_id INTEGER NOT NULL REFERENCES application_events(id) ON DELETE RESTRICT,
    status VARCHAR(16) NOT NULL,
    ended_after_round_type VARCHAR(16),
    failure_reason VARCHAR(24),
    withdraw_reason VARCHAR(24),
    closed_at DATETIME,
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT uq_interview_processes_applied_event UNIQUE (applied_event_id)
);
CREATE TABLE interview_rounds (
    id INTEGER NOT NULL PRIMARY KEY,
    interview_process_id INTEGER NOT NULL
        REFERENCES interview_processes(id) ON DELETE CASCADE,
    round_index INTEGER NOT NULL,
    round_type VARCHAR(16) NOT NULL,
    custom_round_name VARCHAR(128),
    scheduled_at DATETIME,
    duration_minutes INTEGER,
    completed_at DATETIME,
    status VARCHAR(16) NOT NULL,
    outcome VARCHAR(16) NOT NULL,
    failure_reason VARCHAR(24),
    interviewer_name VARCHAR(128),
    interviewer_role VARCHAR(128),
    location_type VARCHAR(16) NOT NULL,
    meeting_url VARCHAR(1024),
    feedback_text TEXT,
    notes TEXT,
    feedback_tags JSON NOT NULL DEFAULT '[]',
    preparation_json JSON NOT NULL DEFAULT '{}',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
);
"""


def build_v08_database(path: Path) -> None:
    build_v07_database(path)
    con = sqlite3.connect(path)
    try:
        con.executescript(V08_EXTRA)
        # The v0.7 applied event is id 4 in this fixture chain; look it up
        # rather than hardcoding, so a fixture change cannot silently break it.
        applied_id = con.execute(
            "select id from application_events where event_type='applied'"
            " and resume_id is not null limit 1"
        ).fetchone()[0]
        con.execute(
            "INSERT INTO interview_processes (job_id, applied_event_id, status)"
            " VALUES (?,?,?)",
            (1, applied_id, "ongoing"),
        )
        con.execute(
            "INSERT INTO interview_rounds (interview_process_id, round_index,"
            " round_type, status, outcome, location_type)"
            " VALUES (?,?,?,?,?,?)",
            (1, 1, "technical", "completed", "passed", "online"),
        )
        con.commit()
    finally:
        con.close()


@pytest.fixture
def v08_db(tmp_path) -> Path:
    path = tmp_path / "v08.db"
    build_v08_database(path)
    return path


def upgrade_from_v08(engine) -> str:
    """Upgrade to the v0.9 revision exactly - not to head, which now has v1.0."""
    stamp(engine, "0006_interview_pipeline")
    upgrade_to(engine, "0007_offer_management")
    return "upgraded"


TRACKED = (
    "jobs",
    "job_analyses",
    "application_events",
    "resumes",
    "recruiter_conversations",
    "recruiter_messages",
    "career_strategy_changes",
    "interview_processes",
    "interview_rounds",
)


def counts(path: Path) -> dict[str, int]:
    con = sqlite3.connect(path)
    try:
        return {
            table: con.execute(f"select count(*) from {table}").fetchone()[0]
            for table in TRACKED
        }
    finally:
        con.close()


# --------------------------------------------------------------------------


def test_a_v08_database_has_no_offer_tables(v08_db):
    """Guards the fixture: it must really represent the older schema."""
    existing = tables(v08_db)
    assert "interview_processes" in existing
    assert "offers" not in existing
    assert "offer_revisions" not in existing


def test_v09_upgrade_loses_no_rows(v08_db):
    before = counts(v08_db)
    assert before["job_analyses"] == 1, "the fixture really has an analysis to lose"
    assert before["interview_rounds"] == 1
    assert before["recruiter_conversations"] == 1

    engine = create_engine(f"sqlite:///{v08_db.as_posix()}")
    try:
        assert upgrade_from_v08(engine) == "upgraded"
        assert current_revision(engine) == "0007_offer_management"
    finally:
        engine.dispose()

    assert counts(v08_db) == before, "v0.9 must not delete a single existing row"


def test_v09_upgrade_leaves_no_foreign_key_violations(v08_db):
    engine = create_engine(f"sqlite:///{v08_db.as_posix()}")
    try:
        upgrade_from_v08(engine)
        with engine.connect() as conn:
            violations = conn.execute(text("pragma foreign_key_check")).fetchall()
    finally:
        engine.dispose()
    assert violations == []


def test_resume_attribution_survives_the_upgrade(v08_db):
    engine = create_engine(f"sqlite:///{v08_db.as_posix()}")
    try:
        upgrade_from_v08(engine)
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "select resume_id from application_events"
                    " where event_type='applied' and resume_id is not null"
                )
            ).fetchall()
            variant = conn.execute(
                text("select variant_name from resumes where id=1")
            ).scalar()
    finally:
        engine.dispose()

    assert len(rows) == 1 and rows[0][0] == 1
    assert variant == "Cloud版"


def test_interview_rows_survive_the_upgrade(v08_db):
    engine = create_engine(f"sqlite:///{v08_db.as_posix()}")
    try:
        upgrade_from_v08(engine)
        with engine.connect() as conn:
            outcome = conn.execute(
                text("select outcome from interview_rounds where id=1")
            ).scalar()
            status = conn.execute(
                text("select status from interview_processes where id=1")
            ).scalar()
    finally:
        engine.dispose()
    assert outcome == "passed"
    assert status == "ongoing"


def test_recruiter_conversations_survive_the_upgrade(v08_db):
    engine = create_engine(f"sqlite:///{v08_db.as_posix()}")
    try:
        upgrade_from_v08(engine)
        with engine.connect() as conn:
            body = conn.execute(
                text("select raw_text from recruiter_messages where id=1")
            ).scalar()
    finally:
        engine.dispose()
    assert body == "您好，方便聊聊吗？"


def test_the_v09_migration_only_adds_tables(v08_db):
    engine = create_engine(f"sqlite:///{v08_db.as_posix()}")
    try:
        stamp(engine, "0006_interview_pipeline")
        before = {t: columns(v08_db, t) for t in tables(v08_db)}
        upgrade_to(engine, "0007_offer_management")
    finally:
        engine.dispose()

    after = {t: columns(v08_db, t) for t in tables(v08_db)}
    assert set(after) - set(before) == {"offers", "offer_revisions"}
    for table, cols in before.items():
        assert after[table] == cols, f"{table} was altered by an additive migration"


def test_a_real_insert_works_against_the_upgraded_schema(v08_db):
    """The v0.8 lesson: column presence is not the same as a usable schema.

    ``TimestampMixin`` leaves ``created_at`` to a server default. A migration
    that declares the column without one produces a schema that rejects every
    insert - on upgraded databases only.
    """
    engine = create_engine(f"sqlite:///{v08_db.as_posix()}")
    try:
        upgrade_from_v08(engine)
    finally:
        engine.dispose()

    con = sqlite3.connect(v08_db)
    try:
        for table in ("offers", "offer_revisions"):
            defaults = {row[1]: row[4] for row in con.execute(f"pragma table_info({table})")}
            assert defaults["created_at"] is not None, f"{table}.created_at has no default"

        applied_id = con.execute(
            "select id from application_events where event_type='applied' limit 1"
        ).fetchone()[0]

        # Insert with only the genuinely required columns.
        con.execute(
            "INSERT INTO offers (job_id, applied_event_id, status, currency,"
            " remote_policy) VALUES (?,?,?,?,?)",
            (1, applied_id, "received", "CNY", "unknown"),
        )
        offer_id = con.execute("select id from offers").fetchone()[0]
        con.execute(
            "INSERT INTO offer_revisions (offer_id, revision_index, revision_type,"
            " source, currency, base_salary_annual, created_at)"
            " VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP)",
            (offer_id, 1, "initial", "company", "CNY", 300000.0),
        )
        con.commit()

        created, benefits = con.execute(
            "select created_at, benefits_json from offers"
        ).fetchone()
        assert created is not None, "the server default filled created_at in"
        assert benefits == "{}"

        base = con.execute("select base_salary_annual from offer_revisions").fetchone()[0]
        assert base == 300000.0

        assert con.execute("pragma foreign_key_check").fetchall() == []
    finally:
        con.close()


def test_the_offer_tables_enforce_one_offer_per_cycle(v08_db):
    engine = create_engine(f"sqlite:///{v08_db.as_posix()}")
    try:
        upgrade_from_v08(engine)
    finally:
        engine.dispose()

    con = sqlite3.connect(v08_db)
    try:
        indexes = list(con.execute("pragma index_list(offers)"))
        assert any(row[2] for row in indexes), "applied_event_id needs a unique index"
    finally:
        con.close()


def test_no_offer_is_fabricated_from_a_legacy_event(v08_db):
    """A v0.4 offer event carries at most a salary string.

    Turning that into a structured offer with a base, bonus and package would
    be inventing compensation the user never entered.
    """
    con = sqlite3.connect(v08_db)
    try:
        con.execute(
            "INSERT INTO application_events (job_id, event_type, notes, metadata_json)"
            " VALUES (?,?,?,?)",
            (1, "offer", "v0.4 记录的 Offer", '{"offer_salary": "35k"}'),
        )
        con.commit()
    finally:
        con.close()

    engine = create_engine(f"sqlite:///{v08_db.as_posix()}")
    try:
        upgrade_from_v08(engine)
        with engine.connect() as conn:
            offers = conn.execute(text("select count(*) from offers")).scalar()
            revisions = conn.execute(text("select count(*) from offer_revisions")).scalar()
            legacy = conn.execute(
                text("select count(*) from application_events where event_type='offer'")
            ).scalar()
    finally:
        engine.dispose()

    assert (offers, revisions) == (0, 0), "nothing is backfilled"
    assert legacy == 1, "and the original event is untouched"


def test_v09_upgrade_is_idempotent(v08_db):
    engine = create_engine(f"sqlite:///{v08_db.as_posix()}")
    try:
        assert upgrade_from_v08(engine) == "upgraded"
        assert ensure_schema_current(engine) == "upgraded"
        with engine.connect() as conn:
            assert conn.execute(text("select count(*) from job_analyses")).scalar() == 1
    finally:
        engine.dispose()


def test_a_v03_database_reaches_the_offer_schema_in_one_go(v03_db):
    """The oldest supported database still upgrades all the way to v0.9 head."""
    engine = create_engine(f"sqlite:///{v03_db.as_posix()}")
    try:
        assert ensure_schema_current(engine) == "adopted_legacy"
        with engine.connect() as conn:
            assert conn.execute(text("select count(*) from jobs")).scalar() == 1
            assert conn.execute(text("select count(*) from job_analyses")).scalar() == 1
            assert conn.execute(text("select count(*) from offers")).scalar() == 0
            assert conn.execute(text("pragma foreign_key_check")).fetchall() == []
    finally:
        engine.dispose()
    assert "offer_revisions" in tables(v03_db)
