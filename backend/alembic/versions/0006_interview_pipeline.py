"""v0.8: multi-round interview pipeline

Two new tables and nothing else. No existing table is altered, which is the
whole point: v0.7 learned the hard way that Alembic's SQLite batch mode
rebuilds a table by dropping the original, and that DROP fires every
ON DELETE CASCADE pointing at it. A pure CREATE TABLE migration cannot do
that, so resumes, jobs, analyses, application events (and their resume
attribution), recruiter conversations and strategy history all survive
untouched by construction.

``interview_processes.applied_event_id`` is RESTRICT: an application cycle an
interview refers to must not be deletable out from under it.

Nothing is backfilled. Existing ``interview`` ApplicationEvents stay exactly as
they are and are reported as legacy milestones - inventing HR/technical/final
rounds from a generic old event would be fabricating history.

Revision ID: 0006_interview_pipeline
Revises: 0005_resume_variants
Create Date: 2026-08-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0006_interview_pipeline"
down_revision: str | None = "0005_resume_variants"
branch_labels: str | None = None
depends_on: str | None = None


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def upgrade() -> None:
    op.create_table(
        "interview_processes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        # The attribution link: one process belongs to one application cycle.
        sa.Column("applied_event_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("ended_after_round_type", sa.String(length=16), nullable=True),
        sa.Column("failure_reason", sa.String(length=24), nullable=True),
        sa.Column("withdraw_reason", sa.String(length=24), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        # server_default matters: both models use TimestampMixin, which relies
        # on the database to fill these in. Without it an upgraded database
        # rejects every insert while a freshly created one works fine.
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["applied_event_id"], ["application_events.id"], ondelete="RESTRICT"
        ),
        # One process per application cycle - a second would double-count the
        # same candidacy in every funnel.
        sa.UniqueConstraint("applied_event_id", name="uq_interview_processes_applied_event"),
    )
    op.create_index("ix_interview_processes_job_id", "interview_processes", ["job_id"])
    op.create_index(
        "ix_interview_processes_applied_event_id", "interview_processes", ["applied_event_id"]
    )
    op.create_index("ix_interview_processes_status", "interview_processes", ["status"])
    op.create_index("ix_interview_processes_closed_at", "interview_processes", ["closed_at"])
    op.create_index(
        "ix_interview_processes_job_status", "interview_processes", ["job_id", "status"]
    )

    op.create_table(
        "interview_rounds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("interview_process_id", sa.Integer(), nullable=False),
        sa.Column("round_index", sa.Integer(), nullable=False),
        sa.Column("round_type", sa.String(length=16), nullable=False),
        sa.Column("custom_round_name", sa.String(length=128), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("failure_reason", sa.String(length=24), nullable=True),
        sa.Column("interviewer_name", sa.String(length=128), nullable=True),
        sa.Column("interviewer_role", sa.String(length=128), nullable=True),
        sa.Column("location_type", sa.String(length=16), nullable=False),
        # May carry an access token: never logged, never in analytics.
        sa.Column("meeting_url", sa.String(length=1024), nullable=True),
        sa.Column("feedback_text", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("feedback_tags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("preparation_json", sa.JSON(), nullable=False, server_default="{}"),
        # server_default matters: both models use TimestampMixin, which relies
        # on the database to fill these in. Without it an upgraded database
        # rejects every insert while a freshly created one works fine.
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["interview_process_id"], ["interview_processes.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_interview_rounds_interview_process_id", "interview_rounds", ["interview_process_id"]
    )
    op.create_index("ix_interview_rounds_round_type", "interview_rounds", ["round_type"])
    op.create_index("ix_interview_rounds_status", "interview_rounds", ["status"])
    op.create_index("ix_interview_rounds_outcome", "interview_rounds", ["outcome"])
    op.create_index("ix_interview_rounds_scheduled_at", "interview_rounds", ["scheduled_at"])
    op.create_index(
        "ix_interview_rounds_process_index",
        "interview_rounds",
        ["interview_process_id", "round_index"],
    )
    op.create_index(
        "ix_interview_rounds_status_scheduled", "interview_rounds", ["status", "scheduled_at"]
    )

    if _is_sqlite():
        # Cheap insurance: fail the upgrade loudly rather than leave the
        # database quietly inconsistent.
        violations = op.get_bind().execute(sa.text("PRAGMA foreign_key_check")).fetchall()
        if violations:  # pragma: no cover - would mean a broken upgrade
            raise RuntimeError(f"0006 left foreign key violations: {violations!r}")


def downgrade() -> None:
    op.drop_table("interview_rounds")
    op.drop_table("interview_processes")
