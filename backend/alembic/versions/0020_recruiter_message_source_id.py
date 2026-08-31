"""M7: persist stable per-conversation BOSS message identities."""

import sqlalchemy as sa
from alembic import op

revision = "0020_recruiter_message_source_id"
down_revision = "0019_application_attempt_claim"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "recruiter_messages",
        sa.Column("source_message_id", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "uq_recruiter_messages_conversation_source_id",
        "recruiter_messages",
        ["conversation_id", "source_message_id"],
        unique=True,
    )


def downgrade():
    op.drop_index(
        "uq_recruiter_messages_conversation_source_id",
        table_name="recruiter_messages",
    )
    op.drop_column("recruiter_messages", "source_message_id")
