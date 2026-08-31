"""Snapshot candidate-stage policy on search tasks.

Revision ID: 0021_candidate_stage_policy
Revises: 0020_recruiter_message_source_id
"""

from alembic import op
import sqlalchemy as sa


revision = "0021_candidate_stage_policy"
down_revision = "0020_recruiter_message_source_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_search_tasks",
        sa.Column(
            "early_career_policy",
            sa.String(length=16),
            nullable=False,
            server_default="exclude",
        ),
    )


def downgrade() -> None:
    op.drop_column("job_search_tasks", "early_career_policy")
