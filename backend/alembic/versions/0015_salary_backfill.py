"""Add bounded salary backfill maintenance progress tables."""
import sqlalchemy as sa
from alembic import op

revision = "0015_salary_backfill"
down_revision = "0014_bounded_matching"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "salary_backfill_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("total_jobs", sa.Integer(), nullable=False),
        sa.Column("current_job_id", sa.Integer(), sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("processed_jobs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_jobs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unavailable_jobs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_jobs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("session_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("paused_reason", sa.String(64)),
        sa.Column("last_action", sa.String(128)),
        sa.Column("last_error", sa.String(256)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("stopped_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "salary_backfill_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("salary_backfill_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(128)),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", "job_id", name="uq_salary_backfill_run_job"),
    )
    op.create_index("ix_salary_backfill_items_run_id", "salary_backfill_items", ["run_id"])


def downgrade():
    op.drop_index("ix_salary_backfill_items_run_id", table_name="salary_backfill_items")
    op.drop_table("salary_backfill_items")
    op.drop_table("salary_backfill_runs")
