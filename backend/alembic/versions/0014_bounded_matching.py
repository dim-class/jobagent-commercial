"""Add opt-in matching approval/claims without rebuilding existing tables."""
import sqlalchemy as sa
from alembic import op

revision = "0014_bounded_matching"
down_revision = "0013_runner_observability"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("job_search_tasks", sa.Column("match_run_json", sa.JSON(), nullable=True))
    op.add_column("job_search_tasks", sa.Column("match_revision", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    op.drop_column("job_search_tasks", "match_revision")
    op.drop_column("job_search_tasks", "match_run_json")
