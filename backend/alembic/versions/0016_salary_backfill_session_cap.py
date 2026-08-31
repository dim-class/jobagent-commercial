"""Persist the explicitly authorized salary-backfill session cap."""
import sqlalchemy as sa
from alembic import op

revision = "0016_salary_cap"
down_revision = "0015_salary_backfill"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "salary_backfill_runs",
        sa.Column("session_cap", sa.Integer(), nullable=False, server_default="3"),
    )


def downgrade():
    op.drop_column("salary_backfill_runs", "session_cap")
