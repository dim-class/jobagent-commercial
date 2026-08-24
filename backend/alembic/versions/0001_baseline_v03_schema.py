"""baseline: v0.1-v0.3 schema (resumes, jobs, job_analyses, application_events)

This is the starting point, describing the schema as it existed at the end of
v0.3. An existing v0.3 database is stamped with this revision rather than
re-running it - see ``app.db.migrations.ensure_schema_current``.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-08-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "resumes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("file_type", sa.String(length=16), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("parsed_profile_json", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_resumes_content_hash", "resumes", ["content_hash"])
    op.create_index("ix_resumes_is_active", "resumes", ["is_active"])
    op.create_index("ix_resumes_active_created", "resumes", ["is_active", "created_at"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=True),
        sa.Column("source_url", sa.String(length=1024), nullable=True),
        sa.Column("company", sa.String(length=256), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("city", sa.String(length=64), nullable=True),
        sa.Column("salary_text", sa.String(length=128), nullable=True),
        sa.Column("experience_text", sa.String(length=128), nullable=True),
        sa.Column("education_text", sa.String(length=128), nullable=True),
        sa.Column("raw_description", sa.Text(), nullable=False),
        sa.Column("normalized_description", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_hash", name="uq_jobs_content_hash"),
        sa.UniqueConstraint("source", "external_id", name="uq_jobs_source_external_id"),
    )
    op.create_index("ix_jobs_source", "jobs", ["source"])
    op.create_index("ix_jobs_city", "jobs", ["city"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_city_status", "jobs", ["city", "status"])

    op.create_table(
        "job_analyses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("resume_id", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("cache_key", sa.String(length=64), nullable=False),
        sa.Column("overall_score", sa.Integer(), nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cache_key", name="uq_job_analyses_cache_key"),
    )
    op.create_index("ix_job_analyses_job_id", "job_analyses", ["job_id"])
    op.create_index("ix_job_analyses_resume_id", "job_analyses", ["resume_id"])
    op.create_index("ix_job_analyses_overall_score", "job_analyses", ["overall_score"])
    op.create_index("ix_job_analyses_job_created", "job_analyses", ["job_id", "created_at"])

    op.create_table(
        "application_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=24), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_application_events_job_id", "application_events", ["job_id"])


def downgrade() -> None:
    op.drop_table("application_events")
    op.drop_table("job_analyses")
    op.drop_table("jobs")
    op.drop_table("resumes")
