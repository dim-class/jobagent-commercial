"""URL helpers shared by every intake path.

Nothing here ever *fetches* a URL. A URL supplied by the user is metadata: we
sanitise it for storage and read the hostname to label the source, and that is
all. Recruitment sites are browsed by the human, never by the backend.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

#: hostname fragment -> ``Job.source`` value. Matches ``JobSourceName``.
SITE_BY_HOST: tuple[tuple[str, str], ...] = (
    ("zhipin.com", "boss"),
    ("liepin.com", "liepin"),
    ("zhaopin.com", "zhaopin"),
    ("51job.com", "job51"),
    ("jobs.51job.com", "job51"),
)

DEFAULT_SOURCE = "manual"


def redact_url(url: str | None) -> str:
    """Scheme + host + path only. Query strings carry session tokens."""
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:  # pragma: no cover - defensive
        return "<unparseable>"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def canonical_url(url: str | None) -> str | None:
    """Stable URL for storage: no query, no fragment.

    Recruitment sites append per-visit tracking and security parameters
    (BOSS uses ``?lid=…&securityId=…``). Keeping them would leak a
    session-scoped token into the database and make the stored URL useless for
    comparison, so they are stripped before anything is persisted.
    """
    cleaned = redact_url(url)
    return cleaned or None


def host_of(url: str | None) -> str:
    try:
        return (urlsplit(url or "").hostname or "").lower()
    except ValueError:  # pragma: no cover - defensive
        return ""


def detect_source(url: str | None) -> str:
    """Label the source from a hostname. Deterministic; never fetches.

    Unknown or missing hosts fall back to ``manual`` - the job is still
    perfectly valid, we just cannot say which site it came from.
    """
    host = host_of(url)
    if not host:
        return DEFAULT_SOURCE
    for fragment, source in SITE_BY_HOST:
        if host == fragment or host.endswith("." + fragment):
            return source
    return DEFAULT_SOURCE
