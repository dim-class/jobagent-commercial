"""v0.6: career strategy audit + recommendation decisions

Two additive tables. Nothing existing is touched: jobs, analyses, application
events, recruiter conversations/messages/analyses and ``jobs.review_after`` all
survive untouched, so a v0.5 database upgrades by gaining tables only.

Analytics itself needs no schema - it is computed from existing history. These
tables exist purely to record *human decisions*: what the strategy was before
and after a change, and which proposals were already answered.

Revision ID: 0004_strategy_analytics
Revises: 0003_recruiter
Create Date: 2026-08-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004_strategy_analytics"
down_revision: str | None = "0003_recruiter"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "career_strategy_changes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("before_hash", sa.String(length=64), nullable=False),
        sa.Column("after_hash", sa.String(length=64), nullable=False),
        sa.Column("before_json", sa.JSON(), nullable=False),
        sa.Column("after_json", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("recommendation_signature", sa.String(length=128), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_career_strategy_changes_after_hash", "career_strategy_changes", ["after_hash"]
    )

    op.create_table(
        "strategy_recommendation_decisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("signature", sa.String(length=128), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        # One standing answer per proposal; re-deciding updates the row.
        sa.UniqueConstraint("signature", name="uq_strategy_recommendation_signature"),
    )
    op.create_index(
        "ix_strategy_recommendation_decisions_signature",
        "strategy_recommendation_decisions",
        ["signature"],
    )


def downgrade() -> None:
    op.drop_table("strategy_recommendation_decisions")
    op.drop_table("career_strategy_changes")
