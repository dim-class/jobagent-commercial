"""M3: 编排会话记录 - orchestration_events

One new, additive table. Nothing existing is altered, so - same as 0009 -
there is no SQLite batch-mode rebuild and no cascade-drop risk; a plain
``create_table`` is enough.

``orchestration_events`` is an append-only audit trail of explicit human
actions taken inside the console: opening a task or one of its candidates,
marking it reviewed, or dismissing it. ``job_id`` is nullable because a
task-level event (e.g. "opened the task itself") has no single candidate;
when it is set, the application layer (not this migration) verifies the
``(task_id, job_id)`` pair is an existing ``task_candidates`` association
before writing. Rows are never updated or deleted.

Revision ID: 0010_orchestration_events
Revises: 0009_job_search_tasks
Create Date: 2026-08-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0010_orchestration_events"
down_revision: str | None = "0009_job_search_tasks"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "orchestration_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["task_id"], ["job_search_tasks.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_orchestration_events_task_id", "orchestration_events", ["task_id"])
    op.create_index("ix_orchestration_events_job_id", "orchestration_events", ["job_id"])

    if op.get_bind().dialect.name == "sqlite":
        violations = op.get_bind().execute(sa.text("PRAGMA foreign_key_check")).fetchall()
        if violations:  # pragma: no cover - purely additive, should never trip
            raise RuntimeError(f"0010 left foreign key violations: {violations!r}")


def downgrade() -> None:
    op.drop_table("orchestration_events")
