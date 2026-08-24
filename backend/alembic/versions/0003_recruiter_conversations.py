"""v0.5: recruiter conversations

Three new tables. Nothing existing is touched - jobs, analyses, application
events and queue state (``jobs.review_after``) are all left exactly as they
are, so a v0.4 database upgrades by gaining tables and losing nothing.

``EventType.candidate_reply`` needs no DDL: the enum columns are
``native_enum=False`` with no CHECK constraint, so new values are just strings.

Revision ID: 0003_recruiter
Revises: 0002_application_queue
Create Date: 2026-08-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0003_recruiter"
down_revision: str | None = "0002_application_queue"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "recruiter_conversations",
        sa.Column("id", sa.Integer(), nullable=False),
        # SET NULL, not CASCADE: deleting a job should not destroy the record
        # of what a recruiter actually said.
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("recruiter_name", sa.String(length=128), nullable=True),
        sa.Column("company", sa.String(length=256), nullable=True),
        sa.Column("title", sa.String(length=256), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("close_reason", sa.String(length=64), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_action_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_recruiter_conversations_job_id", "recruiter_conversations", ["job_id"])
    op.create_index("ix_recruiter_conversations_status", "recruiter_conversations", ["status"])
    op.create_index(
        "ix_recruiter_conversations_last_message_at", "recruiter_conversations", ["last_message_at"]
    )
    op.create_index(
        "ix_recruiter_conversations_next_action_at", "recruiter_conversations", ["next_action_at"]
    )
    op.create_index(
        "ix_recruiter_conversations_status_next",
        "recruiter_conversations",
        ["status", "next_action_at"],
    )

    op.create_table(
        "recruiter_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("source_message_time_text", sa.String(length=128), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["recruiter_conversations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        # Duplicate protection is scoped to one thread on purpose: "好的，谢谢"
        # in two unrelated conversations is two real messages.
        sa.UniqueConstraint(
            "conversation_id",
            "direction",
            "content_hash",
            name="uq_recruiter_messages_conversation_content",
        ),
    )
    op.create_index("ix_recruiter_messages_conversation_id", "recruiter_messages", ["conversation_id"])
    op.create_index("ix_recruiter_messages_content_hash", "recruiter_messages", ["content_hash"])

    op.create_table(
        "recruiter_message_analyses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("resume_id", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("cache_key", sa.String(length=64), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["recruiter_messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cache_key", name="uq_recruiter_message_analyses_cache_key"),
    )
    op.create_index(
        "ix_recruiter_message_analyses_message_id", "recruiter_message_analyses", ["message_id"]
    )


def downgrade() -> None:
    op.drop_table("recruiter_message_analyses")
    op.drop_table("recruiter_messages")
    op.drop_table("recruiter_conversations")
