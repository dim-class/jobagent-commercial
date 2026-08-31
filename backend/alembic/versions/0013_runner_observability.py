"""M4f: runner observability columns on job_search_tasks

Purely additive columns (plain ``ADD COLUMN``, no SQLite batch-mode rebuild -
see CLAUDE.md's migration hazards). Every column is nullable or carries a
``server_default``, so every pre-existing task (manual or SearchPlan) is
unaffected. These fields are deliberately narrow observability only - never
a page URL's query string (which BOSS uses to carry session tokens),
never cookies/storage, never a full job description. See
``services/search_task_runner.report_state``.

Revision ID: 0013_runner_observability
Revises: 0012_search_plan
Create Date: 2026-08-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0013_runner_observability"
down_revision: str | None = "0012_search_plan"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "job_search_tasks", sa.Column("current_url", sa.String(length=512), nullable=True)
    )
    op.add_column(
        "job_search_tasks",
        sa.Column("scroll_round", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "job_search_tasks",
        sa.Column("visible_jobs", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "job_search_tasks",
        sa.Column("imported_jobs", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "job_search_tasks",
        sa.Column("current_candidate", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "job_search_tasks", sa.Column("last_action", sa.String(length=128), nullable=True)
    )
    op.add_column(
        "job_search_tasks", sa.Column("paused_reason", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    for column in (
        "paused_reason",
        "last_action",
        "current_candidate",
        "imported_jobs",
        "visible_jobs",
        "scroll_round",
        "current_url",
    ):
        op.drop_column("job_search_tasks", column)
