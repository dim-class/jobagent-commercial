"""M4a: supervised-session scaffolding - supervised_sessions + supervised_session_events

Two new, additive tables. Nothing existing is altered, so - same as 0009/0010
- there is no SQLite batch-mode rebuild and no cascade-drop risk; a plain
``create_table`` is enough.

``supervised_sessions`` records one human-approved, bounded M4a session: the
task it is for, the caps the human approved (independently re-checked
against the immutable POC ceilings in the application layer), the exact
``https://www.zhipin.com`` tab origin it is scoped to, an immutable snapshot
of the task's criteria at approval time (``approved_criteria_json`` - never
a URL or a token), and zero-valued progress counters - M4a never navigates,
so nothing advances them yet.

``supervised_session_events`` is the append-only audit trail, exactly like
``application_events`` and ``orchestration_events``: rows are never updated
or deleted.

Revision ID: 0011_supervised_sessions
Revises: 0010_orchestration_events
Create Date: 2026-08-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0011_supervised_sessions"
down_revision: str | None = "0010_orchestration_events"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "supervised_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("page_cap", sa.Integer(), nullable=False),
        sa.Column("candidate_cap", sa.Integer(), nullable=False),
        sa.Column("scroll_cap", sa.Integer(), nullable=False),
        sa.Column("tab_origin", sa.String(length=64), nullable=False),
        sa.Column(
            "approved_criteria_json", sa.JSON(), nullable=False, server_default="{}"
        ),
        sa.Column("pages_visited", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("candidates_extracted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scrolls_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stop_reason", sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["task_id"], ["job_search_tasks.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_supervised_sessions_task_id", "supervised_sessions", ["task_id"]
    )

    op.create_table(
        "supervised_session_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["session_id"], ["supervised_sessions.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_supervised_session_events_session_id",
        "supervised_session_events",
        ["session_id"],
    )

    if op.get_bind().dialect.name == "sqlite":
        violations = op.get_bind().execute(sa.text("PRAGMA foreign_key_check")).fetchall()
        if violations:  # pragma: no cover - purely additive, should never trip
            raise RuntimeError(f"0011 left foreign key violations: {violations!r}")


def downgrade() -> None:
    op.drop_table("supervised_session_events")
    op.drop_table("supervised_sessions")
