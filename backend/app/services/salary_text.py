"""Deterministic salary-text validity.

BOSS renders some salary figures with a per-page obfuscation font: the DOM text
holds Private Use Area code points (U+E000-U+F8FF) that only *look* like digits
because of a downloaded font. Copied out - or read by the extension, or by local
screenshot OCR that fell back to the same glyphs - they arrive as
``"-K"`` and are shown as ``□□-□□K``.

Such a value is not a salary. It must never be stored, never overwrite a real
salary, and the job it belongs to must stay eligible for the existing bounded
salary backfill / OCR flow.

Pure functions: no AI, no network, no database, no file access.
"""

from __future__ import annotations

# Private Use Areas (BMP + both supplementary planes) plus the glyphs a viewer
# actually sees when a font is missing or a decoder gave up.
_PUA_RANGES = ((0xE000, 0xF8FF), (0xF0000, 0xFFFFD), (0x100000, 0x10FFFD))
_PLACEHOLDER_CHARS = frozenset("�■□▢▣▫▭▯◻◼⬛⬜￼")

#: Real salaries that legitimately carry no digit. Mirrors
#: ``NEGOTIABLE_SALARY_TOKENS`` in ``extension/src/boss/selectors.ts``.
NEGOTIABLE_SALARY_TOKENS = ("面议", "薪资面议")


def contains_placeholder_glyphs(value: str) -> bool:
    """True when the text carries an unrenderable / obfuscated-font code point."""
    for char in value:
        if char in _PLACEHOLDER_CHARS:
            return True
        code = ord(char)
        if any(low <= code <= high for low, high in _PUA_RANGES):
            return True
    return False


def is_valid_salary_text(value: str | None) -> bool:
    """Whether ``value`` is a salary a human could actually read.

    Mirrors ``isUsableSalary()`` in ``extension/src/boss/extract.ts`` so the
    browser side and the database side agree on what counts as a salary.
    """
    if not value:
        return False
    text = value.strip()
    if not text or contains_placeholder_glyphs(text):
        return False
    if any(char.isdigit() for char in text):
        return True
    return text in NEGOTIABLE_SALARY_TOKENS


def sanitize_salary_text(value: str | None) -> str | None:
    """Return the salary unchanged, or ``None`` when it is not a real salary.

    Dropping the value - rather than storing the boxes - is what puts the job
    back into the existing missing-salary backfill plan.
    """
    return value if is_valid_salary_text(value) else None
