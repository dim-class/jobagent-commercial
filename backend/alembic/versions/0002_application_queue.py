"""v0.4: application decision queue

Two additive columns. Existing jobs and analyses are untouched:

  * ``application_events.metadata_json`` - structured, event-specific detail
    (skip_reason, interview_round, response_type, ...) in one JSON column
    instead of a nullable column per event kind. Existing rows get ``{}``.
  * ``jobs.review_after`` - when 稍后处理 should put a job back in the queue.
    NULL for every existing job, which means "eligible now".

Revision ID: 0002_application_queue
Revises: 0001_baseline
Create Date: 2026-08-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0002_application_queue"
down_revision: str | None = "0001_baseline"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "application_events",
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column("jobs", sa.Column("review_after", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_jobs_review_after", "jobs", ["review_after"])


def downgrade() -> None:
    op.drop_index("ix_jobs_review_after", table_name="jobs")
    op.drop_column("jobs", "review_after")
    op.drop_column("application_events", "metadata_json")
