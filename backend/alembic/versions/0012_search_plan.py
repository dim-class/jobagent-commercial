"""M4e/M4f: SearchPlan + bounded automatic runner columns on job_search_tasks

Purely additive columns on an existing table - plain ``ADD COLUMN``, not a
SQLite batch-mode rebuild, so no cascade-drop risk (see CLAUDE.md's migration
hazards). Every new column is nullable or carries a ``server_default``, so
every pre-existing manual (M1) task is completely unaffected: ``city_id`` and
``run_status`` stay ``NULL``, ``is_search_plan`` stays ``false``, and every
counter stays ``0``.

Revision ID: 0012_search_plan
Revises: 0011_supervised_sessions
Create Date: 2026-08-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0012_search_plan"
down_revision: str | None = "0011_supervised_sessions"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("job_search_tasks", sa.Column("city_id", sa.String(length=16), nullable=True))
    op.add_column(
        "job_search_tasks",
        sa.Column(
            "is_search_plan", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "job_search_tasks", sa.Column("run_status", sa.String(length=24), nullable=True)
    )
    op.add_column(
        "job_search_tasks",
        sa.Column("run_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "job_search_tasks",
        sa.Column("run_stopped_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "job_search_tasks",
        sa.Column("observed_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "job_search_tasks",
        sa.Column("new_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "job_search_tasks",
        sa.Column("duplicate_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "job_search_tasks",
        sa.Column("no_new_rounds", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "job_search_tasks", sa.Column("last_error", sa.String(length=256), nullable=True)
    )


def downgrade() -> None:
    for column in (
        "last_error",
        "no_new_rounds",
        "duplicate_count",
        "new_count",
        "observed_count",
        "run_stopped_at",
        "run_started_at",
        "run_status",
        "is_search_plan",
        "city_id",
    ):
        op.drop_column("job_search_tasks", column)
