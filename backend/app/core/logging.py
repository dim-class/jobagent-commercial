"""Structured-ish logging for the local app.

Rules (also enforced by review, see CLAUDE.md):
  * never log the API key or any secret;
  * never log a whole resume or a whole job description - log hashes/lengths.

The redaction filter scrubs credential *values*, not mentions: a line such as
``OPENAI_API_KEY 未配置`` must stay readable, while ``api_key=sk-live-...``
must not survive.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

_CONFIGURED = False

_REDACTED = "***redacted***"

# An OpenAI-style key literal anywhere in the line.
_KEY_LITERAL_RE = re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}")
# ``<credential-name> = <value>`` / ``<credential-name>: <value>``
_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|apikey|secret|token|authorization|password)\b"
    r"(\s*[=:]\s*|\s+)"
    r"((?:bearer\s+|basic\s+)?[^\s,;)\]'\"]{6,})"
)
# A bare ``Bearer <token>`` with no field name in front of it.
_BEARER_RE = re.compile(r"(?i)\b(bearer|basic)\s+([A-Za-z0-9._\-+/=]{8,})")
# A ``kv`` field *name* that means "this whole value is a credential" -
# regardless of what the value looks like. Catches e.g. ``api_key="plain
# text"`` or a nested ``{"token": "plain text"}``, which the value-shape
# regexes above would never flag on their own.
#
# Matched as *whole segments* (split on non-alphanumerics), never a bare
# substring - `token` alone would otherwise also flag a perfectly safe
# `tokens_used` usage counter, which must survive redaction untouched.
_CREDENTIAL_FIELD_WORDS = {"apikey", "key", "secret", "token", "authorization", "password"}
_FIELD_SEGMENT_RE = re.compile(r"[^A-Za-z0-9]+")


def _is_credential_field(key: object) -> bool:
    if not isinstance(key, str):
        return False
    segments = (seg.lower() for seg in _FIELD_SEGMENT_RE.split(key) if seg)
    return any(seg in _CREDENTIAL_FIELD_WORDS for seg in segments)


def redact(message: str) -> str:
    """Return ``message`` with any credential-looking value replaced."""
    scrubbed = _KEY_LITERAL_RE.sub(_REDACTED, message)
    scrubbed = _ASSIGNMENT_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}", scrubbed)
    scrubbed = _BEARER_RE.sub(lambda m: f"{m.group(1)} {_REDACTED}", scrubbed)
    return scrubbed


def _scrub_kv_value(key: object, value: Any) -> Any:
    """Recursively scrub one ``log_event`` keyword value.

    The credential-name check on ``key`` runs *before* any recursion: a
    credential-named field (``api_key``, ``token``, a nested ``authorization``,
    ...) is replaced wholesale, container or not - e.g.
    ``authorization={"bearer": "..."}`` must be redacted as a whole because
    ``authorization`` itself is the credential-named field, regardless of what
    its nested keys are named. Checking the key only *after* already having
    recursed into a dict/list value (the previous ordering) would miss this
    exact shape, since the outer field name is never re-examined once nested.

    Every other, non-credential-named field still recurses into dicts/lists
    so a *nested* field name is caught by its own key, and a plain string
    still goes through ``redact()`` for an embedded credential-shaped
    substring. Nested dict keys are not always strings (e.g. an int-keyed
    mapping) - ``_is_credential_field`` treats a non-string key as "not a
    credential name" rather than raising.
    """
    if _is_credential_field(key):
        return _REDACTED
    if isinstance(value, dict):
        return {k: _scrub_kv_value(k, v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_scrub_kv_value(key, v) for v in value)
    if isinstance(value, str):
        return redact(value)
    return value


class _RedactFilter(logging.Filter):
    """Last line of defence against a secret reaching stdout.

    Scrubs both the base message and every ``kv`` value ``log_event``
    attaches - a filter runs before the formatter builds the final
    ``key=value`` line, so redacting only ``record.getMessage()`` would leave
    a secret passed as a keyword argument (e.g. ``log_event(..., api_key=...)``)
    completely unredacted in the appended ``key=value`` pairs. ``kv`` scrubbing
    is by *field name* as well as value shape and recurses into nested
    dicts/lists (see ``_scrub_kv_value``), so ``log_event(..., api_key="plain
    text", nested={"token": "plain text"})`` is fully redacted even though
    neither value looks like a credential on its own.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = str(record.getMessage())
        except Exception:  # pragma: no cover - defensive
            return True
        scrubbed = redact(message)
        if scrubbed != message:
            record.msg = scrubbed
            record.args = ()

        kv = getattr(record, "kv", None)
        if isinstance(kv, dict):
            record.kv = {k: _scrub_kv_value(k, v) for k, v in kv.items()}
        return True


class _KeyValueFormatter(logging.Formatter):
    """``ts level logger | message key=value ...`` - greppable without a parser."""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extra: dict[str, Any] = getattr(record, "kv", {}) or {}
        if extra:
            pairs = " ".join(f"{k}={_fmt(v)}" for k, v in extra.items())
            return f"{base} {pairs}"
        return base


def _fmt(value: Any) -> str:
    text = str(value)
    if any(ch.isspace() for ch in text):
        return f'"{text}"'
    return text


def ensure_utf8_stdout() -> None:
    """Windows consoles default to a legacy codepage, which mangles 中文 output."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - detached/redirected stream
            pass


def setup_logging(level: str = "INFO", *, force: bool = False) -> None:
    """Install the app's structured, redacted handler on the root logger.

    Idempotent by default (``_CONFIGURED`` guards a second call from doing
    anything). ``force=True`` reinstalls it even if already configured -
    Alembic's ``fileConfig`` (invoked by every migration, including the one
    ``init_db()`` runs at startup) reconfigures the root logger's own
    handlers/formatter from ``alembic.ini`` regardless of
    ``disable_existing_loggers``, which silently drops this formatter and the
    redaction filter along with it. ``db/migrations.py`` calls this with
    ``force=True`` right after every migration invocation so a structured,
    redacted log line survives startup instead of quietly degrading to a
    plain root ``Formatter`` at ``WARNING``.
    """
    global _CONFIGURED
    if _CONFIGURED and not force:
        return
    ensure_utf8_stdout()

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(
        _KeyValueFormatter(
            fmt="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    handler.addFilter(_RedactFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # uvicorn brings its own handlers; make them use ours instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True

    for noisy in ("openai", "httpx", "httpx2", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **kv: Any) -> None:
    """Emit one structured event line: ``event key=value key=value``."""
    logger.log(level, event, extra={"kv": kv})
