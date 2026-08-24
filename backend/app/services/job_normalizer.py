"""Deterministic job-description normalization.

Goals, in order:
  1. never destroy information - ``raw_description`` is always preserved;
  2. produce a stable, readable ``normalized_description`` for prompting;
  3. produce a stable ``content_hash`` for duplicate detection.

Only *safe* punctuation normalization happens here: exotic whitespace, bullet
glyphs and curly quotes/dashes are folded to plain equivalents. CJK punctuation
(，。、：；（）！？「」) is left exactly as written.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.hashing import job_content_hash

# Targeted, lossless-in-meaning substitutions for the glyphs that routinely
# show up when a JD is copied out of a recruiting site.
_PUNCT_MAP = {
    # Whitespace look-alikes that break naive splitting.
    " ": " ",   # no-break space
    "　": " ",   # ideographic space
    "​": "",    # zero-width space
    "﻿": "",    # BOM
    # Bullet glyphs -> a single markdown-ish bullet.
    "•": "- ",
    "●": "- ",
    "▪": "- ",
    "‣": "- ",
    "◦": "- ",
    # Dash / quote look-alikes.
    "–": "-",
    "—": "-",
    "－": "-",
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
}
# CJK punctuation is deliberately left alone - ，。、：；（）！？「」 are the
# correct characters in a Chinese JD, and rewriting them to ASCII would both
# mangle the text shown in the UI and change what the model reads.

# Symbol bullets only. Numbered lists keep their numbers: a JD that says
# "满足第 3 条即可" would otherwise become unreadable.
_BULLET_LINE_RE = re.compile(r"^\s*[\-*+·•‣▪◦]\s+")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")

_CITY_SUFFIXES = ("市", "区", "县")
_KNOWN_CITY_ALIASES = {
    "bj": "北京",
    "beijing": "北京",
    "sh": "上海",
    "shanghai": "上海",
    "gz": "广州",
    "guangzhou": "广州",
    "hz": "杭州",
    "hangzhou": "杭州",
    "sz": "深圳",
    "shenzhen": "深圳",
    "remote": "远程",
    "在家办公": "远程",
}


@dataclass(slots=True)
class NormalizedJob:
    company: str
    title: str
    city: str | None
    salary_text: str | None
    experience_text: str | None
    education_text: str | None
    raw_description: str
    normalized_description: str
    content_hash: str
    source_url: str | None


def normalize_whitespace_text(text: str) -> str:
    """Fold unicode oddities, trim lines, collapse runs of blank lines."""
    if not text:
        return ""
    for src, dst in _PUNCT_MAP.items():
        text = text.replace(src, dst)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    lines: list[str] = []
    for line in text.split("\n"):
        line = _MULTI_SPACE_RE.sub(" ", line).strip()
        if _BULLET_LINE_RE.match(line):
            # Unify every bullet style to "- ", keeping numbered lists readable.
            content = _BULLET_LINE_RE.sub("", line, count=1).strip()
            line = f"- {content}" if content else ""
        lines.append(line)

    text = "\n".join(lines)
    text = _MULTI_BLANK_RE.sub("\n\n", text)
    return text.strip()


def normalize_field(value: str | None, *, max_len: int = 128) -> str | None:
    if value is None:
        return None
    cleaned = normalize_whitespace_text(value).replace("\n", " ").strip()
    if not cleaned:
        return None
    return cleaned[:max_len]


def normalize_city(city: str | None) -> str | None:
    """Canonical city label: strip the 市/区 suffix, map common aliases."""
    cleaned = normalize_field(city, max_len=64)
    if not cleaned:
        return None
    lowered = cleaned.lower()
    if lowered in _KNOWN_CITY_ALIASES:
        return _KNOWN_CITY_ALIASES[lowered]
    # "北京·朝阳区" / "上海 - 浦东" -> take the leading segment
    head = re.split(r"[·\-/,，|]", cleaned)[0].strip()
    if not head:
        head = cleaned
    for suffix in _CITY_SUFFIXES:
        if len(head) > 2 and head.endswith(suffix):
            head = head[: -len(suffix)]
            break
    return head[:64]


def normalize_job(
    *,
    company: str,
    title: str,
    raw_description: str,
    city: str | None = None,
    salary_text: str | None = None,
    experience_text: str | None = None,
    education_text: str | None = None,
    source_url: str | None = None,
) -> NormalizedJob:
    """Normalize one manually-pasted job posting."""
    norm_company = normalize_field(company, max_len=256) or ""
    norm_title = normalize_field(title, max_len=256) or ""
    norm_desc = normalize_whitespace_text(raw_description)

    return NormalizedJob(
        company=norm_company,
        title=norm_title,
        city=normalize_city(city),
        salary_text=normalize_field(salary_text),
        experience_text=normalize_field(experience_text),
        education_text=normalize_field(education_text),
        raw_description=raw_description or "",
        normalized_description=norm_desc,
        content_hash=job_content_hash(
            company=norm_company, title=norm_title, normalized_description=norm_desc
        ),
        source_url=normalize_field(source_url, max_len=1024),
    )
