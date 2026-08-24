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


def redact(message: str) -> str:
    """Return ``message`` with any credential-looking value replaced."""
    scrubbed = _KEY_LITERAL_RE.sub(_REDACTED, message)
    scrubbed = _ASSIGNMENT_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}", scrubbed)
    scrubbed = _BEARER_RE.sub(lambda m: f"{m.group(1)} {_REDACTED}", scrubbed)
    return scrubbed


class _RedactFilter(logging.Filter):
    """Last line of defence against a secret reaching stdout."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = str(record.getMessage())
        except Exception:  # pragma: no cover - defensive
            return True
        scrubbed = redact(message)
        if scrubbed != message:
            record.msg = scrubbed
            record.args = ()
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


def setup_logging(level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
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
