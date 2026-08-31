"""Clear stored salaries that are obfuscated-font placeholders, not salaries.

BOSS renders some figures with a per-page font whose glyphs live in the Unicode
Private Use Area. Captured before the extractor rejected them, those rows hold
strings the job library can only draw as boxes ("[][]-[][]K"). The digits are
not recoverable from the stored text, so the honest value is "unknown": the row
is reset to NULL and the job becomes eligible for the existing bounded salary
backfill / local OCR flow again.

Data-only. No column, index or constraint changes; readable salaries are never
touched.
"""
import sqlalchemy as sa
from alembic import op

revision = "0017_unreadable_salary"
down_revision = "0016_salary_cap"
branch_labels = None
depends_on = None

# Kept in sync with ``app.services.salary_text``; a migration must not import
# application code, which is free to change after this revision has run.
_PUA_RANGES = ((0xE000, 0xF8FF), (0xF0000, 0xFFFFD), (0x100000, 0x10FFFD))
_PLACEHOLDER_CHARS = frozenset("�■□▢▣▫▭▯◻◼⬛⬜￼")
_NEGOTIABLE = ("面议", "薪资面议")


def _unreadable(value: str | None) -> bool:
    if not value:
        return False
    text = value.strip()
    if not text:
        return False
    for char in text:
        if char in _PLACEHOLDER_CHARS:
            return True
        code = ord(char)
        if any(low <= code <= high for low, high in _PUA_RANGES):
            return True
    if any(char.isdigit() for char in text):
        return False
    return text not in _NEGOTIABLE


def upgrade():
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, salary_text FROM jobs WHERE salary_text IS NOT NULL AND salary_text != ''")
    ).fetchall()
    stale = [row[0] for row in rows if _unreadable(row[1])]
    for job_id in stale:
        bind.execute(
            sa.text("UPDATE jobs SET salary_text = NULL WHERE id = :id"), {"id": job_id}
        )


def downgrade():
    # The placeholder text carried no recoverable information; there is nothing
    # to restore.
    pass
