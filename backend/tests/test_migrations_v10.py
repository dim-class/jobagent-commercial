"""v0.9 -> v1.0 schema upgrade.

0008 does two things, and both have a history of going wrong in this repo:

* it **rebuilds** ``interview_processes`` to turn ``applied_event_id`` from
  RESTRICT into CASCADE. A SQLite batch rebuild drops the table, which fires
  every cascade pointing at it and silently drops every index it had. v0.7 lost
  ``job_analyses`` rows exactly this way;
* it **adds** three tables whose timestamps come from a server default. A
  migration that omits ``server_default`` produces a schema that rejects every
  insert - on upgraded databases only, which is to say on real ones.

So the assertions below go past column presence: real rows, real ORM writes,
and a real ``DELETE`` of the job that used to be undeletable.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.db.migrations import current_revision, ensure_schema_current

from tests.test_migrations import columns, stamp, tables, upgrade_to, v03_db  # noqa: F401
from tests.test_migrations_v09 import build_v08_database


def build_v09_database(path: Path) -> None:
    """A v0.8 fixture brought up to 0007, then given a real offer."""
    build_v08_database(path)
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    try:
        stamp(engine, "0006_interview_pipeline")
        upgrade_to(engine, "0007_offer_management")
    finally:
        engine.dispose()

    con = sqlite3.connect(path)
    try:
        applied_id = con.execute(
            "select id from application_events where event_type='applied' limit 1"
        ).fetchone()[0]
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
    finally:
        con.close()


@pytest.fixture
def v09_db(tmp_path) -> Path:
    path = tmp_path / "v09.db"
    build_v09_database(path)
    return path


def upgrade(path: Path) -> str:
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    try:
        return ensure_schema_current(engine)
    finally:
        engine.dispose()


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
    "offers",
    "offer_revisions",
)


def counts(path: Path) -> dict[str, int]:
    con = sqlite3.connect(path)
    try:
        return {t: con.execute(f"select count(*) from {t}").fetchone()[0] for t in TRACKED}
    finally:
        con.close()


def foreign_keys(path: Path, table: str) -> dict[str, str]:
    """``{column: on_delete}`` for one table."""
    con = sqlite3.connect(path)
    try:
        return {row[3]: row[6] for row in con.execute(f"pragma foreign_key_list({table})")}
    finally:
        con.close()


def indexes(path: Path, table: str) -> set[str]:
    con = sqlite3.connect(path)
    try:
        return {
            row[0]
            for row in con.execute(
                "select name from sqlite_master where type='index' and tbl_name=?",
                (table,),
            )
        }
    finally:
        con.close()


# --------------------------------------------------------------------------
# the fixture itself
# --------------------------------------------------------------------------


def test_a_v09_database_really_looks_like_one(v09_db):
    assert "offers" in tables(v09_db)
    assert "decision_profiles" not in tables(v09_db)
    assert foreign_keys(v09_db, "interview_processes")["applied_event_id"] == "RESTRICT"
    assert counts(v09_db)["offers"] == 1


# --------------------------------------------------------------------------
# the interview foreign key
# --------------------------------------------------------------------------


def test_the_interview_foreign_key_becomes_cascade(v09_db):
    assert upgrade(v09_db) == "upgraded"
    assert current_revision_of(v09_db) == "0022_resume_direction_analyses"

    keys = foreign_keys(v09_db, "interview_processes")
    assert keys["applied_event_id"] == "CASCADE"
    assert keys["job_id"] == "CASCADE"


def current_revision_of(path: Path) -> str:
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    try:
        return current_revision(engine)
    finally:
        engine.dispose()


def test_the_rebuild_keeps_every_interview_index(v09_db):
    """A batch rebuild drops indexes silently - every query would table-scan."""
    before = indexes(v09_db, "interview_processes")
    upgrade(v09_db)
    after = indexes(v09_db, "interview_processes")
    assert before <= after, f"lost {before - after}"
    assert "ix_interview_processes_job_status" in after


def test_the_rebuild_keeps_the_one_process_per_cycle_constraint(v09_db):
    upgrade(v09_db)
    con = sqlite3.connect(v09_db)
    try:
        row = con.execute(
            "select sql from sqlite_master where type='table'"
            " and name='interview_processes'"
        ).fetchone()[0]
        assert "uq_interview_processes_applied_event" in row
    finally:
        con.close()


def test_the_rebuild_loses_no_rows(v09_db):
    before = counts(v09_db)
    assert before["interview_processes"] == 1
    assert before["interview_rounds"] == 1
    assert before["job_analyses"] == 1

    upgrade(v09_db)
    assert counts(v09_db) == before


def test_the_rebuild_does_not_orphan_the_offer(v09_db):
    """``offers.interview_process_id`` points at the rebuilt table."""
    upgrade(v09_db)
    con = sqlite3.connect(v09_db)
    try:
        assert con.execute("pragma foreign_key_check").fetchall() == []
    finally:
        con.close()


# --------------------------------------------------------------------------
# the new tables
# --------------------------------------------------------------------------


def test_the_upgrade_only_adds_the_decision_tables(v09_db):
    before = {t: columns(v09_db, t) for t in TRACKED}
    upgrade(v09_db)

    assert set(tables(v09_db)) - set(before) >= {
        "decision_profiles",
        "offer_assessments",
        "decision_snapshots",
    }
    for table, cols in before.items():
        expected = cols | ({"source_message_id"} if table == "recruiter_messages" else set())
        assert columns(v09_db, table) == expected, f"{table} changed shape"


def test_the_new_timestamps_have_server_defaults(v09_db):
    """The v0.8 lesson, asserted rather than remembered."""
    upgrade(v09_db)
    con = sqlite3.connect(v09_db)
    try:
        for table in ("decision_profiles", "offer_assessments", "decision_snapshots"):
            defaults = {
                row[1]: row[4] for row in con.execute(f"pragma table_info({table})")
            }
            assert defaults["created_at"] is not None, f"{table}.created_at"
    finally:
        con.close()


# --------------------------------------------------------------------------
# real ORM work against the upgraded file
# --------------------------------------------------------------------------


def session_on(path: Path):
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    return engine, Session(bind=engine, future=True)


def test_the_orm_can_read_and_write_the_upgraded_database(v09_db):
    """Column inspection proves nothing about whether the app can use it."""
    from app.models import (
        DecisionProfile,
        DecisionSnapshot,
        InterviewProcess,
        Job,
        Offer,
        OfferAssessment,
    )

    upgrade(v09_db)
    engine, db = session_on(v09_db)
    try:
        # Pre-existing rows are readable through the current mappers.
        job = db.get(Job, 1)
        assert job is not None
        offer = db.scalars(select_all(Offer)).first()
        assert offer is not None and offer.revisions

        process = db.scalars(select_all(InterviewProcess)).first()
        assert process is not None and process.rounds

        # And the v1.0 tables accept real writes - defaults included.
        profile = DecisionProfile(
            name="默认偏好",
            is_active=True,
            weights_json={"compensation": 5},
            deal_breakers_json=[],
            fx_rates_json={"JPY": 0.05},
        )
        assessment = OfferAssessment(
            offer_id=offer.id, ratings_json={"career_growth": 4}, minimum_total_cash=1.0
        )
        snapshot = DecisionSnapshot(
            name="升级后", offer_ids_json=[offer.id], payload_json={"version": 1}
        )
        db.add_all([profile, assessment, snapshot])
        db.commit()

        db.expire_all()
        assert db.get(DecisionProfile, profile.id).created_at is not None
        assert db.get(OfferAssessment, assessment.id).ratings_json == {
            "career_growth": 4
        }
        assert db.get(DecisionSnapshot, snapshot.id).payload_json == {"version": 1}
        assert offer.assessment is not None
    finally:
        db.close()
        engine.dispose()


def select_all(model):
    from sqlalchemy import select

    return select(model)


def test_deleting_a_job_works_on_the_upgraded_database(v09_db):
    """The bug 0008 exists to fix, against a database that really upgraded."""
    from app.models import InterviewProcess, InterviewRound, Job, Offer, OfferAssessment

    upgrade(v09_db)
    engine, db = session_on(v09_db)
    try:
        offer = db.scalars(select_all(Offer)).first()
        db.add(OfferAssessment(offer_id=offer.id, ratings_json={"role_fit": 3}))
        db.commit()

        db.delete(db.get(Job, 1))
        db.commit()

        assert db.scalars(select_all(InterviewProcess)).first() is None
        assert db.scalars(select_all(InterviewRound)).first() is None
        assert db.scalars(select_all(Offer)).first() is None
        assert db.scalars(select_all(OfferAssessment)).first() is None
        assert db.execute(text("pragma foreign_key_check")).fetchall() == []
    finally:
        db.close()
        engine.dispose()


# --------------------------------------------------------------------------
# idempotence and the long path
# --------------------------------------------------------------------------


def test_the_upgrade_is_idempotent(v09_db):
    """Running it twice must not rebuild anything a second time, or lose rows."""
    assert upgrade(v09_db) == "upgraded"
    before = counts(v09_db)
    assert upgrade(v09_db) == "upgraded"
    assert counts(v09_db) == before
    assert foreign_keys(v09_db, "interview_processes")["applied_event_id"] == "CASCADE"


def test_a_v03_database_reaches_the_decision_schema_in_one_go(v03_db):
    """The oldest supported database still upgrades all the way - never deleted."""
    engine = create_engine(f"sqlite:///{v03_db.as_posix()}")
    try:
        assert ensure_schema_current(engine) == "adopted_legacy"
    finally:
        engine.dispose()

    assert current_revision_of(v03_db) == "0022_resume_direction_analyses"
    assert {"decision_profiles", "offer_assessments", "decision_snapshots"} <= tables(
        v03_db
    )
    assert foreign_keys(v03_db, "interview_processes")["applied_event_id"] == "CASCADE"
