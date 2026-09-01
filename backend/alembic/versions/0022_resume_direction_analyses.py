"""Cache the résumé -> search-direction analysis.

Revision ID: 0022_resume_direction_analyses
Revises: 0021_candidate_stage_policy
"""

from alembic import op
import sqlalchemy as sa


revision = "0022_resume_direction_analyses"
down_revision = "0021_candidate_stage_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain CREATE TABLE, never batch mode: a rebuild fires every ON DELETE
    # CASCADE pointing at the table being rebuilt (see CLAUDE.md, 0005).
    op.create_table(
        "resume_direction_analyses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cache_key", sa.String(length=64), nullable=False),
        sa.Column(
            "resume_id",
            sa.Integer(),
            sa.ForeignKey("resumes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        # TimestampMixin leaves these to the database, so the migration has to
        # repeat the server defaults or every insert fails on upgraded
        # databases only - exactly the ones real users have.
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_resume_direction_analyses_cache_key",
        "resume_direction_analyses",
        ["cache_key"],
        unique=True,
    )
    op.create_index(
        "ix_resume_direction_analyses_resume_id",
        "resume_direction_analyses",
        ["resume_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_resume_direction_analyses_resume_id", "resume_direction_analyses")
    op.drop_index("ix_resume_direction_analyses_cache_key", "resume_direction_analyses")
    op.drop_table("resume_direction_analyses")
