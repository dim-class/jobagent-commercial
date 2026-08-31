"""Persist M6 greeting provenance and the one-attempt atomic claim."""
import sqlalchemy as sa
from alembic import op

revision = "0019_application_attempt_claim"
down_revision = "0018_application_approval"
branch_labels = None
depends_on = None


def upgrade():
    # Existing development approvals predate the explicit user-attestation
    # rule and must not silently become executable.
    op.add_column(
        "application_approvals",
        sa.Column(
            "answers_source",
            sa.String(length=48),
            nullable=False,
            server_default="legacy_unspecified",
        ),
    )
    op.add_column(
        "application_approvals",
        sa.Column("attempt_started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("application_approvals", "attempt_started_at")
    op.drop_column("application_approvals", "answers_source")
