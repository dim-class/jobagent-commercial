"""URL helpers shared by every intake path.

Nothing here ever *fetches* a URL. A URL supplied by the user is metadata: we
sanitise it for storage and read the hostname to label the source, and that is
all. Recruitment sites are browsed by the human, never by the backend.
"""

from __future__ import annotations

import re

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


def is_openable_posting_url(url: str | None, *, external_id: str | None) -> bool:
    """Whether this URL can be offered to the user as the posting's own page.

    A stored URL is not automatically a working link. Leftover fixture URLs from
    live verification (``/job_detail/live1.html``) and captures that never
    resolved an id both sit in the database looking plausible, and a queue that
    links to them sends the user to a page that does not exist.

    For BOSS the identity rule is the one ``salary_backfill`` already applies:
    the path must be exactly ``/job_detail/<external_id>.html`` on
    ``www.zhipin.com``, so the link is backed by the same id the row is keyed
    on. The site is read from the URL's own host rather than the stored
    ``Job.source`` label, because a manually pasted BOSS link is stored as
    ``manual`` and deserves the same check. Other sites only need a plain
    http(s) URL - we cannot verify their shape and must not pretend to.
    """
    if not url:
        return False
    try:
        parts = urlsplit(url)
    except ValueError:  # pragma: no cover - defensive
        return False
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return False
    if detect_source(url) != "boss":
        return True
    return (
        parts.scheme == "https"
        and parts.netloc == "www.zhipin.com"
        and bool(external_id)
        and parts.path == f"/job_detail/{external_id}.html"
    )


def boss_external_id(url: str | None) -> str | None:
    """The job id in a BOSS detail URL, or None if this is not one.

    The inverse of the path rule `is_openable_posting_url` enforces, kept
    beside it so the two cannot drift: exactly
    ``https://www.zhipin.com/job_detail/<external_id>.html``. Anything else -
    a search page, a live-preview page, another host - is not an identity.
    """
    canonical = canonical_url(url)
    if not canonical:
        return None
    parts = urlsplit(canonical)
    if parts.scheme != "https" or parts.netloc != "www.zhipin.com":
        return None
    # Character class taken from the ids actually stored, not guessed: 367 real
    # BOSS ids are 28 chars over [A-Za-z0-9_~-]. Omitting `~` silently reported
    # a stored job as new, which is the one thing this must never do.
    match = re.fullmatch(r"/job_detail/([A-Za-z0-9_~-]{1,128})\.html", parts.path)
    return match.group(1) if match else None


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
