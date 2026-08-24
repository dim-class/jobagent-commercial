"""v0.9: offers and negotiation revisions

Two new tables, `CREATE TABLE` only. No existing table is altered, so the
SQLite batch-rebuild cascade that cost v0.7 its ``job_analyses`` rows cannot
happen here: jobs, resumes, analyses, application events (with their resume
attribution), interview processes/rounds, recruiter conversations and strategy
history all survive by construction.

Two things this migration gets right on purpose, both learned the hard way:

- ``created_at`` / ``updated_at`` carry ``server_default``. ``TimestampMixin``
  leaves them to the database, so omitting it produces a schema that rejects
  every insert - and only on *upgraded* databases, which is exactly what real
  users have. v0.8 shipped that bug briefly;
- ``offers.accepted_revision_id`` references ``offer_revisions`` while
  ``offer_revisions.offer_id`` references ``offers``. SQLite resolves foreign
  keys lazily, so the forward reference is fine, but the tables are still
  created in dependency order and the result is checked.

Nothing is backfilled. Existing ``offer`` ApplicationEvents stay exactly as
they are - inventing base salaries and bonus structures from a milestone that
carries at most a salary string would be fabricating compensation history.

Revision ID: 0007_offer_management
Revises: 0006_interview_pipeline
Create Date: 2026-08-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0007_offer_management"
down_revision: str | None = "0006_interview_pipeline"
branch_labels: str | None = None
depends_on: str | None = None


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def _timestamps() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "offers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        # The attribution link: one offer belongs to one application cycle.
        sa.Column("applied_event_id", sa.Integer(), nullable=False),
        sa.Column("interview_process_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("proposed_start_date", sa.Date(), nullable=True),
        sa.Column("employment_type", sa.String(length=16), nullable=True),
        sa.Column("work_location", sa.String(length=256), nullable=True),
        sa.Column("remote_policy", sa.String(length=16), nullable=False),
        sa.Column("probation_text", sa.String(length=512), nullable=True),
        sa.Column("benefits_json", sa.JSON(), nullable=False, server_default="{}"),
        # Decision snapshots - frozen so later edits cannot move what a past
        # decision was made on.
        sa.Column("accepted_revision_id", sa.Integer(), nullable=True),
        sa.Column("declined_revision_id", sa.Integer(), nullable=True),
        sa.Column("decline_reason", sa.String(length=24), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["applied_event_id"], ["application_events.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["interview_process_id"], ["interview_processes.id"], ondelete="SET NULL"
        ),
        # SET NULL, not RESTRICT: offers and offer_revisions reference each
        # other, so RESTRICT here left the two tables with no valid delete
        # order and made a job carrying an accepted offer undeletable.
        sa.ForeignKeyConstraint(
            ["accepted_revision_id"], ["offer_revisions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["declined_revision_id"], ["offer_revisions.id"], ondelete="SET NULL"
        ),
        # One offer per application cycle.
        sa.UniqueConstraint("applied_event_id", name="uq_offers_applied_event"),
    )
    op.create_index("ix_offers_job_id", "offers", ["job_id"])
    op.create_index("ix_offers_applied_event_id", "offers", ["applied_event_id"])
    op.create_index("ix_offers_interview_process_id", "offers", ["interview_process_id"])
    op.create_index("ix_offers_status", "offers", ["status"])
    op.create_index("ix_offers_currency", "offers", ["currency"])
    op.create_index("ix_offers_decision_deadline", "offers", ["decision_deadline"])
    op.create_index("ix_offers_decided_at", "offers", ["decided_at"])
    op.create_index("ix_offers_job_status", "offers", ["job_id", "status"])

    op.create_table(
        "offer_revisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("offer_id", sa.Integer(), nullable=False),
        sa.Column("revision_index", sa.Integer(), nullable=False),
        sa.Column("revision_type", sa.String(length=24), nullable=False),
        # A candidate counter is a request, not an offer. Everything that reads
        # "the current offer" filters on this.
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("base_salary_annual", sa.Float(), nullable=True),
        sa.Column("base_salary_monthly", sa.Float(), nullable=True),
        sa.Column("months_per_year", sa.Integer(), nullable=True),
        sa.Column("bonus_guaranteed", sa.Float(), nullable=True),
        sa.Column("bonus_target", sa.Float(), nullable=True),
        sa.Column("signing_bonus", sa.Float(), nullable=True),
        sa.Column("stock_value", sa.Float(), nullable=True),
        sa.Column("stock_type", sa.String(length=24), nullable=True),
        sa.Column("stock_vesting_years", sa.Float(), nullable=True),
        sa.Column("stock_vesting_text", sa.String(length=512), nullable=True),
        sa.Column("allowances_annual", sa.Float(), nullable=True),
        sa.Column("overtime_pay_text", sa.String(length=512), nullable=True),
        sa.Column("housing_value", sa.Float(), nullable=True),
        sa.Column("transport_value", sa.Float(), nullable=True),
        sa.Column("other_cash_annual", sa.Float(), nullable=True),
        sa.Column("salary_text_original", sa.Text(), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("requested_start_date", sa.Date(), nullable=True),
        sa.Column("requested_remote_policy", sa.String(length=16), nullable=True),
        sa.Column("other_request", sa.Text(), nullable=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("corrects_revision_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["offer_id"], ["offers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["corrects_revision_id"], ["offer_revisions.id"], ondelete="SET NULL"
        ),
    )
    op.create_index("ix_offer_revisions_offer_id", "offer_revisions", ["offer_id"])
    op.create_index("ix_offer_revisions_revision_type", "offer_revisions", ["revision_type"])
    op.create_index("ix_offer_revisions_source", "offer_revisions", ["source"])
    op.create_index(
        "ix_offer_revisions_offer_index", "offer_revisions", ["offer_id", "revision_index"]
    )
    op.create_index(
        "ix_offer_revisions_offer_source", "offer_revisions", ["offer_id", "source"]
    )

    if _is_sqlite():
        violations = op.get_bind().execute(sa.text("PRAGMA foreign_key_check")).fetchall()
        if violations:  # pragma: no cover - would mean a broken upgrade
            raise RuntimeError(f"0007 left foreign key violations: {violations!r}")


def downgrade() -> None:
    op.drop_table("offer_revisions")
    op.drop_table("offers")
