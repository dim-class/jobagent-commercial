"""M1: 求职任务控制台 - job_search_tasks + task_candidates

Two new, additive tables. Nothing existing is altered, so there is no SQLite
batch-mode rebuild and no cascade-drop risk (see the 0005/0008 hazards this
repo has already hit) - a plain ``create_table`` is enough.

``job_search_tasks`` stores stored search criteria (keywords, city,
experience, education, salary band, exclusions, resume variant, a candidate
cap, a minimum score) plus an explicit, currently single-valued ``mode``.
Creating or reading a row never searches, captures, or navigates anything.

``task_candidates`` is a many-to-many association between tasks and jobs,
never a ``task_id`` column on ``jobs``: the repo globally deduplicates
``Job`` by ``content_hash`` / ``(source, external_id)``, and the same
posting can legitimately be discovered under two different tasks. A
``UNIQUE(task_id, job_id)`` constraint makes attaching the same job to the
same task twice a no-op at the application layer rather than a duplicate row.

``exclusions_json`` is nullable with no default: unlike the JSON columns in
0008 (which default to ``{}``/``[]`` because "nothing configured yet" and
"explicitly empty" are the same thing there), M1 keeps "never set" (``NULL``)
and "explicitly no exclusions" (``[]``) distinguishable end-to-end.

Revision ID: 0009_job_search_tasks
Revises: 0008_decision_support
Create Date: 2026-08-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0009_job_search_tasks"
down_revision: str | None = "0008_decision_support"
branch_labels: str | None = None
depends_on: str | None = None


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
        "job_search_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("keywords", sa.String(length=256), nullable=True),
        sa.Column("city", sa.String(length=64), nullable=True),
        sa.Column("experience_text", sa.String(length=64), nullable=True),
        sa.Column("education_text", sa.String(length=64), nullable=True),
        sa.Column("salary_min", sa.Integer(), nullable=True),
        sa.Column("salary_max", sa.Integer(), nullable=True),
        # No default: NULL (never set) and [] (explicitly no exclusions)
        # must stay distinguishable, so nothing coerces this at write time.
        sa.Column("exclusions_json", sa.JSON(), nullable=True),
        sa.Column("resume_id", sa.Integer(), nullable=True),
        sa.Column("max_candidates", sa.Integer(), nullable=True),
        sa.Column("min_score", sa.Integer(), nullable=True),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_job_search_tasks_resume_id", "job_search_tasks", ["resume_id"])

    op.create_table(
        "task_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["task_id"], ["job_search_tasks.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("task_id", "job_id", name="uq_task_candidates_task_job"),
    )
    op.create_index("ix_task_candidates_task_id", "task_candidates", ["task_id"])
    op.create_index("ix_task_candidates_job_id", "task_candidates", ["job_id"])

    if op.get_bind().dialect.name == "sqlite":
        violations = op.get_bind().execute(sa.text("PRAGMA foreign_key_check")).fetchall()
        if violations:  # pragma: no cover - purely additive, should never trip
            raise RuntimeError(f"0009 left foreign key violations: {violations!r}")


def downgrade() -> None:
    op.drop_table("task_candidates")
    op.drop_table("job_search_tasks")
