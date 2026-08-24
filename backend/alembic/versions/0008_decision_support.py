"""v1.0: offer decision support, and the interview FK fix

Two unrelated things, deliberately in one revision so an upgraded database is
never left half-way between them.

**1. ``interview_processes.applied_event_id`` becomes CASCADE.**

It was RESTRICT, which never protected the attribution it claimed to: the
``applied`` event and the interview process are both children of the same job,
so the ORM removed the events first and the RESTRICT simply made the job
undeletable. v0.9 fixed the identical problem on ``offers``; this closes the
same hole on interviews.

SQLite cannot ALTER a constraint, so Alembic rebuilds the table: create temp,
copy, DROP original, rename. That DROP fires every ON DELETE CASCADE pointing
at ``interview_processes`` - which would take ``interview_rounds`` with it, and
NULL every ``offers.interview_process_id``. Foreign keys are ON in this app
(db/session.py), so they are suspended for the rebuild and the result is
verified. This is exactly the failure mode that cost v0.7 its ``job_analyses``
rows.

**2. Three new tables** for decision support. Additive, so nothing can be lost:
``decision_profiles`` (weights, deal-breakers, user-entered FX),
``offer_assessments`` (1-5 subjective ratings and negotiation targets), and
``decision_snapshots`` (frozen comparisons).

``created_at`` / ``updated_at`` carry ``server_default`` - the models use
``TimestampMixin``, which leaves them to the database, and omitting it produces
a schema that rejects every insert on upgraded databases only.

Revision ID: 0008_decision_support
Revises: 0007_offer_management
Create Date: 2026-08-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0008_decision_support"
down_revision: str | None = "0007_offer_management"
branch_labels: str | None = None
depends_on: str | None = None


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def _interview_processes_table(applied_event_ondelete: str) -> sa.Table:
    """The v0.8 table, with the applied-event FK action as a parameter.

    Batch mode rebuilds from this definition, which is how a SQLite constraint
    gets changed at all.
    """
    return sa.Table(
        "interview_processes",
        sa.MetaData(),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("applied_event_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("ended_after_round_type", sa.String(length=16), nullable=True),
        sa.Column("failure_reason", sa.String(length=24), nullable=True),
        sa.Column("withdraw_reason", sa.String(length=24), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
            ["applied_event_id"],
            ["application_events.id"],
            ondelete=applied_event_ondelete,
        ),
        sa.UniqueConstraint(
            "applied_event_id", name="uq_interview_processes_applied_event"
        ),
    )


#: Indexes 0006 created on ``interview_processes``. A batch rebuild drops them,
#: so they have to be put back explicitly or every interview query silently
#: degrades to a table scan.
_INTERVIEW_INDEXES: tuple[tuple[str, list[str]], ...] = (
    ("ix_interview_processes_job_id", ["job_id"]),
    ("ix_interview_processes_applied_event_id", ["applied_event_id"]),
    ("ix_interview_processes_status", ["status"]),
    ("ix_interview_processes_closed_at", ["closed_at"]),
    ("ix_interview_processes_job_status", ["job_id", "status"]),
)


def _recreate_interview_indexes() -> None:
    existing = {
        row[0]
        for row in op.get_bind().execute(
            sa.text(
                "select name from sqlite_master where type='index'"
                " and tbl_name='interview_processes'"
            )
        )
    }
    for name, columns in _INTERVIEW_INDEXES:
        if name not in existing:
            op.create_index(name, "interview_processes", columns)


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


def _row_counts(conn) -> dict[str, int]:
    """Counts of everything the interview rebuild could damage."""
    tables = ("interview_processes", "interview_rounds", "offers", "application_events")
    counts: dict[str, int] = {}
    for table in tables:
        exists = conn.execute(
            sa.text("select count(*) from sqlite_master where type='table' and name=:n"),
            {"n": table},
        ).scalar()
        if exists:
            counts[table] = conn.execute(
                sa.text(f"select count(*) from {table}")  # noqa: S608 - fixed names
            ).scalar()
    return counts


def upgrade() -> None:
    conn = op.get_bind()
    before = _row_counts(conn) if _is_sqlite() else {}

    # --- 1. the interview FK ---------------------------------------------
    if _is_sqlite():
        op.execute(sa.text("PRAGMA foreign_keys=OFF"))

    # 0006 created the foreign keys inline and unnamed, so there is no name to
    # drop. Handing batch mode the full target definition rebuilds the table
    # from it instead.
    # recreate="always" is required: with no column operations inside the
    # block, batch mode would decide it had nothing to do and leave the old
    # constraint in place.
    with op.batch_alter_table(
        "interview_processes",
        copy_from=_interview_processes_table("CASCADE"),
        recreate="always",
    ):
        pass

    # A batch rebuild drops every index that was not part of ``copy_from``.
    _recreate_interview_indexes()

    if _is_sqlite():
        op.execute(sa.text("PRAGMA foreign_keys=ON"))
        after = _row_counts(conn)
        if after != before:
            raise RuntimeError(
                f"0008 lost rows during the interview rebuild: {before} -> {after}"
            )
        violations = conn.execute(sa.text("PRAGMA foreign_key_check")).fetchall()
        if violations:  # pragma: no cover - would mean a broken upgrade
            raise RuntimeError(f"0008 left foreign key violations: {violations!r}")

    # --- 2. decision support ----------------------------------------------
    op.create_table(
        "decision_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        # Raw weights as typed; normalized only at calculation time.
        sa.Column("weights_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("deal_breakers_json", sa.JSON(), nullable=False, server_default="[]"),
        # User-entered exchange rates. v1.0 looks nothing up.
        sa.Column("fx_rates_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("base_currency", sa.String(length=8), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_decision_profiles_is_active", "decision_profiles", ["is_active"])

    op.create_table(
        "offer_assessments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("offer_id", sa.Integer(), nullable=False),
        # {dimension: 1..5}. Absent means unrated, never zero.
        sa.Column("ratings_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("target_total_cash", sa.Float(), nullable=True),
        sa.Column("ideal_total_cash", sa.Float(), nullable=True),
        sa.Column("minimum_total_cash", sa.Float(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["offer_id"], ["offers.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("offer_id", name="uq_offer_assessments_offer"),
    )
    op.create_index("ix_offer_assessments_offer_id", "offer_assessments", ["offer_id"])

    op.create_table(
        "decision_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("offer_ids_json", sa.JSON(), nullable=False, server_default="[]"),
        # The whole frozen computation. Never rewritten.
        sa.Column("payload_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_decision_snapshots_created", "decision_snapshots", ["created_at"])

    if _is_sqlite():
        violations = conn.execute(sa.text("PRAGMA foreign_key_check")).fetchall()
        if violations:  # pragma: no cover
            raise RuntimeError(f"0008 left foreign key violations: {violations!r}")


def downgrade() -> None:
    op.drop_table("decision_snapshots")
    op.drop_table("offer_assessments")
    op.drop_table("decision_profiles")

    if _is_sqlite():
        op.execute(sa.text("PRAGMA foreign_keys=OFF"))
    with op.batch_alter_table(
        "interview_processes",
        copy_from=_interview_processes_table("RESTRICT"),
        recreate="always",
    ):
        pass
    if _is_sqlite():
        op.execute(sa.text("PRAGMA foreign_keys=ON"))
