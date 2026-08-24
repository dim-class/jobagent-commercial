"""v0.7: resume variants + application-time resume attribution

Additive only. Every existing row survives, and **nothing is backfilled**:
``application_events.resume_id`` starts NULL for all historical applications,
which reads as ``ResumeUsage.unknown``. Filling those in from whichever resume
happens to be active today would invent history that never happened - only a
human can supply them, through the corrective
``application_resume_attributed`` event.

``resumes`` gains variant metadata. A Resume row *is* a variant, so no second
table is introduced.

The ``application_events.event_type`` column is widened from 24 to 40 chars:
the new ``application_resume_attributed`` value is 29 characters. SQLite does
not enforce VARCHAR length, so this is a no-op there in practice, but the
declared type should still match the model.

Revision ID: 0005_resume_variants
Revises: 0004_strategy_analytics
Create Date: 2026-08-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0005_resume_variants"
down_revision: str | None = "0004_strategy_analytics"
branch_labels: str | None = None
depends_on: str | None = None


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def upgrade() -> None:
    # SQLite has no ALTER TABLE ADD CONSTRAINT, so Alembic's batch mode
    # rebuilds the table: create temp, copy, DROP original, rename. That DROP
    # fires every ON DELETE CASCADE pointing at it - which would silently wipe
    # job_analyses when "resumes" is rebuilt. Foreign keys are ON in this app
    # (see db/session.py), so they must be suspended for the rebuild.
    if _is_sqlite():
        op.execute(sa.text("PRAGMA foreign_keys=OFF"))

    # --- resumes become named, groupable, forkable, archivable -----------
    with op.batch_alter_table("resumes") as batch:
        batch.add_column(sa.Column("variant_name", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("variant_group", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("parent_resume_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("notes", sa.Text(), nullable=True))
        batch.add_column(sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key(
            "fk_resumes_parent_resume_id",
            "resumes",
            ["parent_resume_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_index("ix_resumes_variant_group", "resumes", ["variant_group"])
    op.create_index("ix_resumes_parent_resume_id", "resumes", ["parent_resume_id"])
    op.create_index("ix_resumes_archived_at", "resumes", ["archived_at"])
    op.create_index("ix_resumes_archived_created", "resumes", ["archived_at", "created_at"])

    # Existing resumes get their filename as a starting variant name, so the
    # UI has something to show. This is a *label*, not an attribution - it
    # says nothing about which resume any past application used.
    op.execute(
        sa.text(
            "UPDATE resumes SET variant_name = filename "
            "WHERE variant_name IS NULL OR variant_name = ''"
        )
    )

    # --- application events carry the resume actually submitted ---------
    with op.batch_alter_table("application_events") as batch:
        batch.alter_column(
            "event_type",
            existing_type=sa.String(length=24),
            type_=sa.String(length=40),
            existing_nullable=False,
        )
        batch.add_column(sa.Column("resume_id", sa.Integer(), nullable=True))
        # RESTRICT, not CASCADE: a resume referenced by a real application
        # must not be deletable - that would silently erase attribution.
        batch.create_foreign_key(
            "fk_application_events_resume_id",
            "resumes",
            ["resume_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    op.create_index("ix_application_events_resume_id", "application_events", ["resume_id"])

    if _is_sqlite():
        op.execute(sa.text("PRAGMA foreign_keys=ON"))
        # Fail loudly rather than leave the database quietly inconsistent.
        violations = op.get_bind().execute(sa.text("PRAGMA foreign_key_check")).fetchall()
        if violations:  # pragma: no cover - would mean a broken upgrade
            raise RuntimeError(f"0005 left foreign key violations: {violations!r}")


def downgrade() -> None:
    if _is_sqlite():
        op.execute(sa.text("PRAGMA foreign_keys=OFF"))
    op.drop_index("ix_application_events_resume_id", table_name="application_events")
    with op.batch_alter_table("application_events") as batch:
        batch.drop_constraint("fk_application_events_resume_id", type_="foreignkey")
        batch.drop_column("resume_id")
        batch.alter_column(
            "event_type",
            existing_type=sa.String(length=40),
            type_=sa.String(length=24),
            existing_nullable=False,
        )

    op.drop_index("ix_resumes_archived_created", table_name="resumes")
    op.drop_index("ix_resumes_archived_at", table_name="resumes")
    op.drop_index("ix_resumes_parent_resume_id", table_name="resumes")
    op.drop_index("ix_resumes_variant_group", table_name="resumes")
    with op.batch_alter_table("resumes") as batch:
        batch.drop_constraint("fk_resumes_parent_resume_id", type_="foreignkey")
        batch.drop_column("archived_at")
        batch.drop_column("notes")
        batch.drop_column("parent_resume_id")
        batch.drop_column("variant_group")
        batch.drop_column("variant_name")

    if _is_sqlite():
        op.execute(sa.text("PRAGMA foreign_keys=ON"))
