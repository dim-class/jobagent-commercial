"""Schema migrations.

The important guarantee: a database created by v0.3 upgrades to v0.4 in place,
keeping every job, analysis and event. Deleting the database is never required.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.db.migrations import (
    BASELINE_REVISION,
    current_revision,
    ensure_schema_current,
    has_legacy_tables,
)

# Exactly the DDL a v0.3 database has: no jobs.review_after, no
# application_events.metadata_json, and no alembic_version table.
V03_SCHEMA = """
CREATE TABLE resumes (
    id INTEGER NOT NULL PRIMARY KEY,
    filename VARCHAR(512) NOT NULL,
    file_type VARCHAR(16) NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    raw_text TEXT NOT NULL,
    parsed_profile_json JSON NOT NULL,
    is_active BOOLEAN NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
);
CREATE TABLE jobs (
    id INTEGER NOT NULL PRIMARY KEY,
    source VARCHAR(32) NOT NULL,
    external_id VARCHAR(128),
    source_url VARCHAR(1024),
    company VARCHAR(256) NOT NULL,
    title VARCHAR(256) NOT NULL,
    city VARCHAR(64),
    salary_text VARCHAR(128),
    experience_text VARCHAR(128),
    education_text VARCHAR(128),
    raw_description TEXT NOT NULL,
    normalized_description TEXT NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT uq_jobs_content_hash UNIQUE (content_hash),
    CONSTRAINT uq_jobs_source_external_id UNIQUE (source, external_id)
);
CREATE TABLE job_analyses (
    id INTEGER NOT NULL PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    resume_id INTEGER NOT NULL REFERENCES resumes(id) ON DELETE CASCADE,
    model VARCHAR(64) NOT NULL,
    prompt_version VARCHAR(32) NOT NULL,
    cache_key VARCHAR(64) NOT NULL,
    overall_score INTEGER NOT NULL,
    verdict VARCHAR(16) NOT NULL,
    result_json JSON NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT uq_job_analyses_cache_key UNIQUE (cache_key)
);
CREATE TABLE application_events (
    id INTEGER NOT NULL PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    event_type VARCHAR(24) NOT NULL,
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
);
"""


def build_v03_database(path: Path) -> None:
    con = sqlite3.connect(path)
    try:
        con.executescript(V03_SCHEMA)
        con.execute(
            "INSERT INTO resumes (filename, file_type, content_hash, raw_text,"
            " parsed_profile_json, is_active) VALUES (?,?,?,?,?,?)",
            ("resume.pdf", "pdf", "r" * 64, "云运维工程师简历", "{}", 1),
        )
        con.execute(
            "INSERT INTO jobs (source, company, title, city, raw_description,"
            " normalized_description, content_hash, status) VALUES (?,?,?,?,?,?,?,?)",
            ("boss", "老数据公司", "云计算工程师", "杭州", "JD 原文", "JD 原文", "j" * 64, "new"),
        )
        con.execute(
            "INSERT INTO job_analyses (job_id, resume_id, model, prompt_version,"
            " cache_key, overall_score, verdict, result_json) VALUES (?,?,?,?,?,?,?,?)",
            (1, 1, "gpt-5.6-luna", "v1", "c" * 64, 88, "apply", '{"overall_score": 88}'),
        )
        con.execute(
            "INSERT INTO application_events (job_id, event_type, notes) VALUES (?,?,?)",
            (1, "analyzed", "旧版本写入的事件"),
        )
        con.commit()
    finally:
        con.close()


@pytest.fixture
def v03_db(tmp_path) -> Path:
    path = tmp_path / "v03.db"
    build_v03_database(path)
    return path


def columns(path: Path, table: str) -> set[str]:
    con = sqlite3.connect(path)
    try:
        return {row[1] for row in con.execute(f"pragma table_info({table})")}
    finally:
        con.close()


# --------------------------------------------------------------------------


def test_a_v03_database_lacks_the_v04_columns(v03_db):
    """Guards the fixture itself: it must really represent the old schema."""
    assert "review_after" not in columns(v03_db, "jobs")
    assert "metadata_json" not in columns(v03_db, "application_events")


def test_legacy_database_is_detected(v03_db):
    engine = create_engine(f"sqlite:///{v03_db.as_posix()}")
    try:
        assert has_legacy_tables(engine) is True
        assert current_revision(engine) is None, "v0.3 was never stamped"
    finally:
        engine.dispose()


def test_v03_database_upgrades_in_place_without_losing_data(v03_db):
    engine = create_engine(f"sqlite:///{v03_db.as_posix()}")
    try:
        action = ensure_schema_current(engine)
        assert action == "adopted_legacy"

        with engine.connect() as conn:
            assert conn.execute(text("select count(*) from jobs")).scalar() == 1
            assert conn.execute(text("select count(*) from job_analyses")).scalar() == 1
            assert conn.execute(text("select count(*) from application_events")).scalar() == 1
            assert conn.execute(text("select count(*) from resumes")).scalar() == 1

            company = conn.execute(text("select company from jobs where id=1")).scalar()
            assert company == "老数据公司"
            note = conn.execute(
                text("select notes from application_events where id=1")
            ).scalar()
            assert note == "旧版本写入的事件"
    finally:
        engine.dispose()

    assert "review_after" in columns(v03_db, "jobs")
    assert "metadata_json" in columns(v03_db, "application_events")


def test_upgraded_rows_get_usable_defaults(v03_db):
    engine = create_engine(f"sqlite:///{v03_db.as_posix()}")
    try:
        ensure_schema_current(engine)
        with engine.connect() as conn:
            assert conn.execute(text("select review_after from jobs where id=1")).scalar() is None
            metadata = conn.execute(
                text("select metadata_json from application_events where id=1")
            ).scalar()
            assert metadata in ("{}", {}), "pre-existing events default to empty metadata"
    finally:
        engine.dispose()


def test_migration_is_idempotent(v03_db):
    engine = create_engine(f"sqlite:///{v03_db.as_posix()}")
    try:
        assert ensure_schema_current(engine) == "adopted_legacy"
        assert ensure_schema_current(engine) == "upgraded"
        assert ensure_schema_current(engine) == "upgraded"
        with engine.connect() as conn:
            assert conn.execute(text("select count(*) from jobs")).scalar() == 1
    finally:
        engine.dispose()


def test_upgrade_clears_only_unreadable_salaries(v03_db):
    """0017: obfuscated-font placeholders become NULL; real salaries survive."""
    boxed = "-K"
    con = sqlite3.connect(v03_db)
    try:
        for index, salary in enumerate((boxed, "16-19K", "面议", None), start=2):
            con.execute(
                "INSERT INTO jobs (source, company, title, salary_text, raw_description,"
                " normalized_description, content_hash, status) VALUES (?,?,?,?,?,?,?,?)",
                ("boss", "公司", "岗位", salary, "JD", "JD", f"{index}" * 64, "new"),
            )
        con.commit()
    finally:
        con.close()

    engine = create_engine(f"sqlite:///{v03_db.as_posix()}")
    try:
        ensure_schema_current(engine)
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT id, salary_text FROM jobs ORDER BY id")
            ).fetchall()
    finally:
        engine.dispose()

    assert [row[1] for row in rows] == [None, None, "16-19K", "面议", None]


def test_a_fresh_database_is_created_and_stamped(tmp_path):
    path = tmp_path / "fresh.db"
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    try:
        assert ensure_schema_current(engine) == "created"
        assert current_revision(engine) is not None
        assert "review_after" in columns(path, "jobs")
    finally:
        engine.dispose()


def test_the_test_database_is_at_head():
    """The suite itself runs against a fully migrated schema."""
    from app.db.session import engine

    assert current_revision(engine) is not None
    assert current_revision(engine) != BASELINE_REVISION, "v0.4 revision must be applied"


# --------------------------------------------------------------------------
# v0.4 -> v0.5
# --------------------------------------------------------------------------

# A v0.4 database: baseline plus the application-queue columns, and no
# recruiter tables at all.
V04_EXTRA = """
ALTER TABLE jobs ADD COLUMN review_after DATETIME;
ALTER TABLE application_events ADD COLUMN metadata_json JSON NOT NULL DEFAULT '{}';
"""


def build_v04_database(path: Path) -> None:
    build_v03_database(path)
    con = sqlite3.connect(path)
    try:
        con.executescript(V04_EXTRA)
        # queue state a v0.4 user would already have
        con.execute("UPDATE jobs SET status='applied' WHERE id=1")
        con.execute(
            "INSERT INTO application_events (job_id, event_type, notes, metadata_json)"
            " VALUES (?,?,?,?)",
            (1, "applied", "v0.4 记录的投递", '{"source": "manual_confirmation"}'),
        )
        con.commit()
    finally:
        con.close()


@pytest.fixture
def v04_db(tmp_path) -> Path:
    path = tmp_path / "v04.db"
    build_v04_database(path)
    return path


def tables(path: Path) -> set[str]:
    con = sqlite3.connect(path)
    try:
        return {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    finally:
        con.close()


def test_a_v04_database_has_no_recruiter_tables(v04_db):
    """Guards the fixture: it must really represent the older schema."""
    assert "review_after" in columns(v04_db, "jobs")
    assert "recruiter_conversations" not in tables(v04_db)


def test_v04_database_upgrades_and_keeps_queue_state(v04_db):
    engine = create_engine(f"sqlite:///{v04_db.as_posix()}")
    try:
        # Stamp it as v0.4 the way a real upgraded database would be.
        from alembic import command

        from app.db.migrations import alembic_config

        command.stamp(alembic_config(engine), "0002_application_queue")
        assert ensure_schema_current(engine) == "upgraded"

        with engine.connect() as conn:
            assert conn.execute(text("select count(*) from jobs")).scalar() == 1
            assert conn.execute(text("select count(*) from job_analyses")).scalar() == 1
            assert conn.execute(text("select count(*) from application_events")).scalar() == 2
            # v0.4 workflow state survives untouched
            assert conn.execute(text("select status from jobs where id=1")).scalar() == "applied"
            note = conn.execute(
                text("select notes from application_events where event_type='applied'")
            ).scalar()
            assert note == "v0.4 记录的投递"
    finally:
        engine.dispose()

    created = tables(v04_db)
    assert "recruiter_conversations" in created
    assert "recruiter_messages" in created
    assert "recruiter_message_analyses" in created


def test_recruiter_upgrade_is_idempotent(v04_db):
    engine = create_engine(f"sqlite:///{v04_db.as_posix()}")
    try:
        from alembic import command

        from app.db.migrations import alembic_config

        command.stamp(alembic_config(engine), "0002_application_queue")
        assert ensure_schema_current(engine) == "upgraded"
        assert ensure_schema_current(engine) == "upgraded"
        with engine.connect() as conn:
            assert conn.execute(text("select count(*) from jobs")).scalar() == 1
    finally:
        engine.dispose()


def test_a_v03_database_reaches_the_recruiter_schema_in_one_go(v03_db):
    """The oldest supported database still upgrades all the way to head."""
    engine = create_engine(f"sqlite:///{v03_db.as_posix()}")
    try:
        assert ensure_schema_current(engine) == "adopted_legacy"
        with engine.connect() as conn:
            assert conn.execute(text("select count(*) from jobs")).scalar() == 1
            assert conn.execute(text("select count(*) from recruiter_conversations")).scalar() == 0
    finally:
        engine.dispose()
    assert "recruiter_messages" in tables(v03_db)


# --------------------------------------------------------------------------
# v0.5 -> v0.6
# --------------------------------------------------------------------------

# A v0.5 database: everything v0.5 had, and no strategy-analytics tables.
V05_EXTRA = """
CREATE TABLE recruiter_conversations (
    id INTEGER NOT NULL PRIMARY KEY,
    job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    source VARCHAR(16) NOT NULL,
    recruiter_name VARCHAR(128),
    company VARCHAR(256),
    title VARCHAR(256),
    status VARCHAR(24) NOT NULL,
    close_reason VARCHAR(64),
    last_message_at DATETIME,
    next_action_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
);
CREATE TABLE recruiter_messages (
    id INTEGER NOT NULL PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES recruiter_conversations(id) ON DELETE CASCADE,
    direction VARCHAR(16) NOT NULL,
    raw_text TEXT NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    source_message_time_text VARCHAR(128),
    captured_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL
);
CREATE TABLE recruiter_message_analyses (
    id INTEGER NOT NULL PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES recruiter_messages(id) ON DELETE CASCADE,
    job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    resume_id INTEGER REFERENCES resumes(id) ON DELETE SET NULL,
    model VARCHAR(64) NOT NULL,
    prompt_version VARCHAR(32) NOT NULL,
    cache_key VARCHAR(64) NOT NULL,
    result_json JSON NOT NULL,
    created_at DATETIME NOT NULL
);
"""


def build_v05_database(path: Path) -> None:
    build_v04_database(path)
    con = sqlite3.connect(path)
    try:
        con.executescript(V05_EXTRA)
        con.execute(
            "INSERT INTO recruiter_conversations (job_id, source, recruiter_name,"
            " company, status) VALUES (?,?,?,?,?)",
            (1, "boss", "王女士", "老数据公司", "needs_reply"),
        )
        con.execute(
            "INSERT INTO recruiter_messages (conversation_id, direction, raw_text,"
            " content_hash, captured_at, created_at)"
            " VALUES (?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
            (1, "recruiter", "您好，方便聊聊吗？", "m" * 64),
        )
        # A reply the human confirmed they sent themselves.
        con.execute(
            "INSERT INTO application_events (job_id, event_type, notes, metadata_json)"
            " VALUES (?,?,?,?)",
            (1, "candidate_reply", "v0.5 记录的回复", "{}"),
        )
        con.commit()
    finally:
        con.close()


@pytest.fixture
def v05_db(tmp_path) -> Path:
    path = tmp_path / "v05.db"
    build_v05_database(path)
    return path


def stamp(engine, revision: str) -> None:
    from alembic import command

    from app.db.migrations import alembic_config

    command.stamp(alembic_config(engine), revision)


def upgrade_to(engine, revision: str) -> None:
    """Upgrade to one specific revision rather than all the way to head."""
    from alembic import command

    from app.db.migrations import alembic_config

    command.upgrade(alembic_config(engine), revision)


def test_a_v05_database_has_no_strategy_tables(v05_db):
    """Guards the fixture: it must really represent the older schema."""
    existing = tables(v05_db)
    assert "recruiter_messages" in existing
    assert "career_strategy_changes" not in existing
    assert "strategy_recommendation_decisions" not in existing


def test_v05_database_upgrades_and_keeps_every_row(v05_db):
    engine = create_engine(f"sqlite:///{v05_db.as_posix()}")
    try:
        stamp(engine, "0003_recruiter")
        assert ensure_schema_current(engine) == "upgraded"

        with engine.connect() as conn:
            assert conn.execute(text("select count(*) from jobs")).scalar() == 1
            assert conn.execute(text("select count(*) from job_analyses")).scalar() == 1
            assert conn.execute(text("select count(*) from application_events")).scalar() == 3
            assert conn.execute(
                text("select count(*) from recruiter_conversations")
            ).scalar() == 1
            assert conn.execute(text("select count(*) from recruiter_messages")).scalar() == 1

            # The v0.4/v0.5 history the analytics engine reads is untouched.
            assert conn.execute(text("select status from jobs where id=1")).scalar() == "applied"
            note = conn.execute(
                text("select notes from application_events where event_type='candidate_reply'")
            ).scalar()
            assert note == "v0.5 记录的回复"
    finally:
        engine.dispose()

    created = tables(v05_db)
    assert "career_strategy_changes" in created
    assert "strategy_recommendation_decisions" in created


def test_the_v06_migration_is_purely_additive(v05_db):
    """No existing table is rewritten - v0.6 only adds two audit tables."""
    engine = create_engine(f"sqlite:///{v05_db.as_posix()}")
    try:
        stamp(engine, "0003_recruiter")
        # Snapshot after stamping, so the alembic bookkeeping table is not
        # mistaken for something the v0.6 migration created.
        before = {t: columns(v05_db, t) for t in tables(v05_db)}
        # Upgrade to 0004 exactly - not to head, which now includes v0.7.
        upgrade_to(engine, "0004_strategy_analytics")
    finally:
        engine.dispose()

    after = {t: columns(v05_db, t) for t in tables(v05_db)}
    for table, cols in before.items():
        assert after[table] == cols, f"{table} was altered by an additive migration"
    assert set(after) - set(before) == {
        "career_strategy_changes",
        "strategy_recommendation_decisions",
    }


def test_strategy_upgrade_is_idempotent(v05_db):
    engine = create_engine(f"sqlite:///{v05_db.as_posix()}")
    try:
        stamp(engine, "0003_recruiter")
        assert ensure_schema_current(engine) == "upgraded"
        assert ensure_schema_current(engine) == "upgraded"
        with engine.connect() as conn:
            assert conn.execute(text("select count(*) from jobs")).scalar() == 1
    finally:
        engine.dispose()


def test_a_v03_database_reaches_the_strategy_schema_in_one_go(v03_db):
    """The oldest supported database still upgrades all the way to v0.6 head."""
    engine = create_engine(f"sqlite:///{v03_db.as_posix()}")
    try:
        assert ensure_schema_current(engine) == "adopted_legacy"
        with engine.connect() as conn:
            assert conn.execute(text("select count(*) from jobs")).scalar() == 1
            assert conn.execute(
                text("select count(*) from career_strategy_changes")
            ).scalar() == 0
    finally:
        engine.dispose()
    assert "strategy_recommendation_decisions" in tables(v03_db)


def test_a_dismissed_signature_is_unique(v05_db):
    """Deciding twice on the same proposal must update, never duplicate."""
    engine = create_engine(f"sqlite:///{v05_db.as_posix()}")
    try:
        stamp(engine, "0003_recruiter")
        ensure_schema_current(engine)
        con = sqlite3.connect(v05_db)
        try:
            rows = list(
                con.execute("pragma index_list(strategy_recommendation_decisions)")
            )
            assert any(row[2] for row in rows), "signature needs a unique index"
        finally:
            con.close()
    finally:
        engine.dispose()


def test_running_migrations_does_not_disable_application_logging():
    """Regression guard.

    Alembic's ``fileConfig`` defaults to ``disable_existing_loggers=True``,
    which switches off every logger already created - the whole ``app.*`` tree
    included. ``init_db()`` runs migrations during startup, so that silently
    killed all application logging for the rest of the process.
    """
    import logging

    from app.db.session import init_db

    # The suite has already run init_db via the session fixture; run it again
    # to be sure the effect is not order-dependent.
    init_db()

    for name in (
        "app",
        "app.services.offer_management",
        "app.services.application_workflow",
        "app.services.interview_pipeline",
    ):
        assert logging.getLogger(name).disabled is False, f"{name} was disabled"


def test_running_migrations_preserves_structured_redacted_logging():
    """Regression guard for a second, subtler defect than the one above:
    Alembic's own ``env.py`` calls ``fileConfig(...)`` for every command it
    runs, which reconfigures the *root* logger's handlers/formatter from
    ``alembic.ini`` regardless of ``disable_existing_loggers`` - the loggers
    stay enabled (the guard above), but a plain root ``Formatter`` at
    ``WARNING`` silently replaces this app's structured, redacted one. A
    ``log_event`` after that point would "succeed" (no exception) while
    quietly losing its ``key=value`` fields and its secret redaction. This
    must be exercised through the real post-migration logging configuration,
    not by calling the formatter directly.
    """
    import io
    import logging

    from app.core.logging import get_logger, log_event
    from app.db.session import init_db

    init_db()  # runs the real Alembic migration path, exactly like startup

    root = logging.getLogger()
    assert root.handlers, "no handler installed on the root logger after init_db()"
    handler = root.handlers[0]

    buffer = io.StringIO()
    original_stream = handler.stream
    handler.stream = buffer
    try:
        logger = get_logger("app.test_migrations")
        log_event(
            logger,
            "test.post_migration_event",
            level=logging.WARNING,
            job_id=42,
            api_key="sk-live-abcdef0123456789",
        )
    finally:
        handler.stream = original_stream

    output = buffer.getvalue()
    assert "job_id=42" in output, f"structured kv fields were lost after migration: {output!r}"
    assert "sk-live-abcdef0123456789" not in output, "a secret survived redaction after migration"
    assert "***redacted***" in output
