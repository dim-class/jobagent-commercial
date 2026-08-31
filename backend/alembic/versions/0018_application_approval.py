"""M6: the per-job human confirmation that authorizes one application.

Plain CREATE TABLE - no batch rebuild, so no ON DELETE CASCADE fires on any
existing table (CLAUDE.md's 0005 hazard).

`created_at` / `updated_at` repeat `TimestampMixin`'s server defaults on
purpose: omitting them produces a schema that rejects every insert on upgraded
databases, which is exactly the second hazard CLAUDE.md records.
"""
import sqlalchemy as sa
from alembic import op

revision = "0018_application_approval"
down_revision = "0017_unreadable_salary"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "application_approvals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("company", sa.String(length=256), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("canonical_url", sa.String(length=1024), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        # RESTRICT: a resume an approval names must not vanish under it.
        sa.Column(
            "resume_id",
            sa.Integer(),
            sa.ForeignKey("resumes.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("resume_hash", sa.String(length=64), nullable=False),
        sa.Column("answers_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("answers_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "state", sa.String(length=16), nullable=False, server_default="pending"
        ),
        sa.Column("invalidated_reason", sa.String(length=64), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=True),
        sa.Column("outcome_detail", sa.String(length=256), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        # SET NULL, not RESTRICT: the event and the approval are both children
        # of the job, so RESTRICT would only block deleting the job.
        sa.Column(
            "applied_event_id",
            sa.Integer(),
            sa.ForeignKey("application_events.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_application_approvals_job_id", "application_approvals", ["job_id"]
    )
    op.create_index(
        "ix_application_approvals_state", "application_approvals", ["state"]
    )


def downgrade():
    op.drop_index("ix_application_approvals_state", table_name="application_approvals")
    op.drop_index("ix_application_approvals_job_id", table_name="application_approvals")
    op.drop_table("application_approvals")
