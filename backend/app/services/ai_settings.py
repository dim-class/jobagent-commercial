"""Let the user set their own AI credentials from the app.

CLAUDE.md's rule is unchanged and is what shapes this module: the key lives
**only** in the backend process, read from `.env`. It is never returned by an
API, never written to SQLite, never logged, and never sent to the frontend -
the UI only ever learns *whether* one is set and what it ends with.

What this adds is the ability to put it there without editing a file and
restarting: `.env` is still the only home, and `get_settings.cache_clear()` is
what makes the change take effect immediately.

Any OpenAI-compatible endpoint is accepted (`OPENAI_BASE_URL`), which is how
this project supports providers other than OpenAI: one protocol, not several
SDKs.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.errors import ValidationError
from app.core.logging import get_logger, log_event
from app.core.paths import BACKEND_DIR

logger = get_logger(__name__)

ENV_PATH: Path = BACKEND_DIR / ".env"

#: Only these may be written from the UI. Anything else in `.env` is left
#: exactly as the user wrote it, comments and order included.
WRITABLE = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL_FAST", "OPENAI_MODEL_SMART")

_URL = re.compile(r"^https://[^\s\"']+$")
_MODEL = re.compile(r"^[A-Za-z0-9._:\-]{1,64}$")


def _has_unsafe_chars(value: str) -> bool:
    """Whitespace or quotes would corrupt the `.env` line itself."""

    return any(c in value for c in (chr(32), chr(34), chr(39), chr(10), chr(13), chr(9)))


def describe(cfg: Settings | None = None) -> dict:
    """What the UI is allowed to know. Never the key itself."""

    cfg = cfg or get_settings()
    key = cfg.openai_api_key or ""
    return {
        "configured": bool(key),
        #: Enough to tell two keys apart when re-entering one, and useless to
        #: anyone who sees the screen.
        "hint": f"…{key[-4:]}" if len(key) >= 4 else "",
        "base_url": cfg.openai_base_url or "",
        "model_fast": cfg.openai_model_fast,
        "model_smart": cfg.openai_model_smart,
        "env_path": str(ENV_PATH),
    }


def _validate(values: dict[str, str]) -> dict[str, str]:
    clean: dict[str, str] = {}
    for name, raw in values.items():
        value = (raw or "").strip()
        if name == "OPENAI_API_KEY":
            #: Deliberately shape-agnostic: every provider spells its keys
            #: differently, and refusing an unfamiliar one would be a guess.
            #: Whitespace and quotes are refused because they would corrupt the
            #: `.env` line.
            if value and (len(value) < 8 or _has_unsafe_chars(value)):
                raise ValidationError("API Key 格式看起来不对（太短或含空格/引号）。")
        elif name == "OPENAI_BASE_URL":
            if value and not _URL.match(value):
                raise ValidationError("接口地址必须是 https:// 开头的完整地址。")
        elif value and not _MODEL.match(value):
            raise ValidationError(f"模型名 {name} 只能包含字母、数字和 . _ : - 。")
        clean[name] = value
    return clean


def save(values: dict[str, str]) -> dict:
    """Write the given keys into `.env`, leaving every other line untouched."""

    clean = _validate({name: values.get(name, "") for name in WRITABLE if name in values})
    if not clean:
        raise ValidationError("没有要保存的内容。")

    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    for name, value in clean.items():
        line = f"{name}={value}"
        for index, existing in enumerate(lines):
            if existing.strip().startswith(f"{name}="):
                lines[index] = line
                break
        else:
            lines.append(line)
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    ENV_PATH.write_text(chr(10).join(lines) + chr(10), encoding="utf-8")

    # `get_settings` is cached for the process; without this the change would
    # only appear after a restart, which is the whole point of this endpoint.
    get_settings.cache_clear()
    log_event(logger, "settings.ai_saved", fields=sorted(clean), key_set=bool(clean.get("OPENAI_API_KEY")))
    return describe()
