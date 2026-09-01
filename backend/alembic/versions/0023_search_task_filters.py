"""Carry BOSS result-page filters on a SearchPlan task.

Revision ID: 0023_search_task_filters
Revises: 0022_resume_direction_analyses
"""

from alembic import op
import sqlalchemy as sa


revision = "0023_search_task_filters"
down_revision = "0022_resume_direction_analyses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain ADD COLUMN, never batch mode: a rebuild fires every ON DELETE
    # CASCADE pointing at the table (CLAUDE.md, 0005). The server default
    # matters - existing tasks must read as "no extra filters", not NULL.
    op.add_column(
        "job_search_tasks",
        sa.Column("search_filters_json", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("job_search_tasks", "search_filters_json")
