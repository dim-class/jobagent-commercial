"""v0.7 -> v0.8 schema upgrade.

v0.7 lost every ``job_analyses`` row to a SQLite batch rebuild before that was
caught. 0006 only issues CREATE TABLE, so it structurally cannot repeat that -
but "structurally cannot" is exactly the kind of claim worth a test, so this
module proves every table survives and no foreign key is left dangling.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.db.migrations import current_revision, ensure_schema_current

from tests.test_migrations import (
    columns,
    stamp,
    tables,
    upgrade_to,
    v03_db,  # noqa: F401 - re-exported fixture
)
from tests.test_migrations_v07 import build_v06_database

#: A v0.7 database: v0.6 plus resume variants and event-level attribution.
V07_EXTRA = """
ALTER TABLE resumes ADD COLUMN variant_name VARCHAR(128);
ALTER TABLE resumes ADD COLUMN variant_group VARCHAR(64);
ALTER TABLE resumes ADD COLUMN parent_resume_id INTEGER REFERENCES resumes(id);
ALTER TABLE resumes ADD COLUMN notes TEXT;
ALTER TABLE resumes ADD COLUMN archived_at DATETIME;
ALTER TABLE application_events ADD COLUMN resume_id INTEGER REFERENCES resumes(id);
"""


def build_v07_database(path: Path) -> None:
    build_v06_database(path)
    con = sqlite3.connect(path)
    try:
        con.executescript(V07_EXTRA)
        con.execute("UPDATE resumes SET variant_name = 'Cloud版' WHERE id = 1")
        # An application that recorded which resume it used - the v0.7
        # attribution that must survive untouched.
        con.execute(
            "INSERT INTO application_events (job_id, event_type, notes, metadata_json,"
            " resume_id) VALUES (?,?,?,?,?)",
            (1, "applied", "v0.7 记录的投递", '{"resume_usage": "used"}', 1),
        )
        con.commit()
    finally:
        con.close()


@pytest.fixture
def v07_db(tmp_path) -> Path:
    path = tmp_path / "v07.db"
    build_v07_database(path)
    return path


def upgrade_from_v07(engine) -> str:
    """Upgrade to the v0.8 revision exactly - not to head, which now has v0.9."""
    stamp(engine, "0005_resume_variants")
    upgrade_to(engine, "0006_interview_pipeline")
    return "upgraded"


def counts(path: Path) -> dict[str, int]:
    con = sqlite3.connect(path)
    try:
        return {
            table: con.execute(f"select count(*) from {table}").fetchone()[0]
            for table in (
                "jobs",
                "job_analyses",
                "application_events",
                "resumes",
                "recruiter_conversations",
                "recruiter_messages",
                "career_strategy_changes",
            )
        }
    finally:
        con.close()


# --------------------------------------------------------------------------


def test_a_v07_database_has_no_interview_tables(v07_db):
    """Guards the fixture: it must really represent the older schema."""
    existing = tables(v07_db)
    assert "resume_id" in columns(v07_db, "application_events")
    assert "interview_processes" not in existing
    assert "interview_rounds" not in existing


def test_v08_upgrade_loses_no_rows(v07_db):
    before = counts(v07_db)
    assert before["job_analyses"] == 1, "the fixture really has an analysis to lose"
    assert before["recruiter_conversations"] == 1

    engine = create_engine(f"sqlite:///{v07_db.as_posix()}")
    try:
        assert upgrade_from_v07(engine) == "upgraded"
        assert current_revision(engine) == "0006_interview_pipeline"
    finally:
        engine.dispose()

    assert counts(v07_db) == before, "v0.8 must not delete a single existing row"


def test_v08_upgrade_leaves_no_foreign_key_violations(v07_db):
    engine = create_engine(f"sqlite:///{v07_db.as_posix()}")
    try:
        upgrade_from_v07(engine)
        with engine.connect() as conn:
            violations = conn.execute(text("pragma foreign_key_check")).fetchall()
    finally:
        engine.dispose()
    assert violations == []


def test_resume_attribution_survives_the_upgrade(v07_db):
    """v0.7's whole point: which resume an application used, frozen in place."""
    engine = create_engine(f"sqlite:///{v07_db.as_posix()}")
    try:
        upgrade_from_v07(engine)
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "select resume_id, metadata_json from application_events"
                    " where event_type='applied' and resume_id is not null"
                )
            ).fetchall()
            variant = conn.execute(
                text("select variant_name from resumes where id=1")
            ).scalar()
    finally:
        engine.dispose()

    assert len(rows) == 1
    assert rows[0][0] == 1
    assert variant == "Cloud版"


def test_recruiter_conversations_survive_the_upgrade(v07_db):
    engine = create_engine(f"sqlite:///{v07_db.as_posix()}")
    try:
        upgrade_from_v07(engine)
        with engine.connect() as conn:
            name = conn.execute(
                text("select recruiter_name from recruiter_conversations where id=1")
            ).scalar()
            body = conn.execute(
                text("select raw_text from recruiter_messages where id=1")
            ).scalar()
    finally:
        engine.dispose()
    assert name == "王女士"
    assert body == "您好，方便聊聊吗？"


def test_the_v08_migration_only_adds_tables(v07_db):
    engine = create_engine(f"sqlite:///{v07_db.as_posix()}")
    try:
        stamp(engine, "0005_resume_variants")
        before = {t: columns(v07_db, t) for t in tables(v07_db)}
        upgrade_to(engine, "0006_interview_pipeline")
    finally:
        engine.dispose()

    after = {t: columns(v07_db, t) for t in tables(v07_db)}
    assert set(after) - set(before) == {"interview_processes", "interview_rounds"}
    for table, cols in before.items():
        assert after[table] == cols, f"{table} was altered by an additive migration"


def test_no_interview_process_is_fabricated(v07_db):
    """A pre-v0.8 interview event stays a bare milestone.

    Inventing HR/technical/final rounds from a generic old event would be
    fabricating history the user never recorded.
    """
    con = sqlite3.connect(v07_db)
    try:
        con.execute(
            "INSERT INTO application_events (job_id, event_type, notes, metadata_json)"
            " VALUES (?,?,?,?)",
            (1, "interview", "v0.4 记录的面试", '{"interview_round": "一面"}'),
        )
        con.commit()
    finally:
        con.close()

    engine = create_engine(f"sqlite:///{v07_db.as_posix()}")
    try:
        upgrade_from_v07(engine)
        with engine.connect() as conn:
            processes = conn.execute(
                text("select count(*) from interview_processes")
            ).scalar()
            rounds = conn.execute(text("select count(*) from interview_rounds")).scalar()
            legacy = conn.execute(
                text("select count(*) from application_events where event_type='interview'")
            ).scalar()
    finally:
        engine.dispose()

    assert (processes, rounds) == (0, 0), "nothing is backfilled"
    assert legacy == 1, "and the original event is untouched"


def test_the_interview_tables_enforce_one_process_per_cycle(v07_db):
    engine = create_engine(f"sqlite:///{v07_db.as_posix()}")
    try:
        upgrade_from_v07(engine)
    finally:
        engine.dispose()

    con = sqlite3.connect(v07_db)
    try:
        indexes = list(con.execute("pragma index_list(interview_processes)"))
        assert any(row[2] for row in indexes), "applied_event_id needs a unique index"
    finally:
        con.close()


def test_v08_upgrade_is_idempotent(v07_db):
    engine = create_engine(f"sqlite:///{v07_db.as_posix()}")
    try:
        assert upgrade_from_v07(engine) == "upgraded"
        assert ensure_schema_current(engine) == "upgraded"
        with engine.connect() as conn:
            assert conn.execute(text("select count(*) from job_analyses")).scalar() == 1
    finally:
        engine.dispose()


def test_a_v03_database_reaches_the_interview_schema_in_one_go(v03_db):
    """The oldest supported database still upgrades all the way to v0.8 head."""
    engine = create_engine(f"sqlite:///{v03_db.as_posix()}")
    try:
        assert ensure_schema_current(engine) == "adopted_legacy"
        with engine.connect() as conn:
            assert conn.execute(text("select count(*) from jobs")).scalar() == 1
            assert conn.execute(text("select count(*) from job_analyses")).scalar() == 1
            assert conn.execute(text("select count(*) from interview_processes")).scalar() == 0
            assert conn.execute(text("pragma foreign_key_check")).fetchall() == []
    finally:
        engine.dispose()
    assert "interview_rounds" in tables(v03_db)



def test_the_migrated_schema_accepts_what_the_models_write(v07_db):
    """Regression guard for migration/model divergence.

    Both interview models use ``TimestampMixin``, which leaves ``created_at``
    to a server default. A fresh test database comes from ``create_all`` and so
    always has that default; a *migrated* database only has whatever the
    migration declared. Omitting it made every insert fail on exactly the
    databases real users have.
    """
    engine = create_engine(f"sqlite:///{v07_db.as_posix()}")
    try:
        upgrade_from_v07(engine)
    finally:
        engine.dispose()

    con = sqlite3.connect(v07_db)
    try:
        for table in ("interview_processes", "interview_rounds"):
            defaults = {
                row[1]: row[4] for row in con.execute(f"pragma table_info({table})")
            }
            assert defaults["created_at"] is not None, f"{table}.created_at has no default"
            assert defaults["updated_at"] is not None, f"{table}.updated_at has no default"

        # And prove it end to end: an insert with only the required columns.
        con.execute(
            "INSERT INTO interview_processes (job_id, applied_event_id, status)"
            " VALUES (?,?,?)",
            (1, 1, "ongoing"),
        )
        con.commit()
        created = con.execute(
            "select created_at from interview_processes"
        ).fetchone()[0]
        assert created is not None
    finally:
        con.close()
