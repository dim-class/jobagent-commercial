"""Stable content hashes.

Everything that participates in the analysis cache key goes through here so
the hashing rules live in exactly one place.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_WS_RE = re.compile(r"\s+")

PROMPT_HASH_SEPARATOR = "\x1f"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_text(text: str) -> str:
    """Whitespace/case-insensitive canonical form used for content hashing.

    Two JDs that differ only in indentation, blank lines or letter case are the
    same job for duplicate-detection purposes.
    """
    return _WS_RE.sub(" ", (text or "").strip()).lower()


def hash_text(text: str) -> str:
    return sha256_text(canonical_text(text))


def hash_json(data: Any) -> str:
    """Order-independent hash of a JSON-serialisable structure."""
    blob = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return sha256_text(blob)


def hash_parts(*parts: str) -> str:
    """Hash an ordered list of already-hashed / short string parts."""
    return sha256_text(PROMPT_HASH_SEPARATOR.join(p or "" for p in parts))


def job_content_hash(*, company: str, title: str, normalized_description: str) -> str:
    """Duplicate-detection hash for a job.

    Company + title are included so two different companies posting a
    boilerplate-identical JD are still two jobs.
    """
    return hash_parts(
        canonical_text(company),
        canonical_text(title),
        canonical_text(normalized_description),
    )


def analysis_cache_key(
    *,
    resume_hash: str,
    strategy_hash: str,
    job_hash: str,
    model: str,
    prompt_version: str,
) -> str:
    """The one cache key used by :mod:`app.services.job_matcher`."""
    return hash_parts(resume_hash, strategy_hash, job_hash, model.strip(), prompt_version.strip())
