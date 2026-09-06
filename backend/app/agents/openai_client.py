"""The one place an OpenAI-compatible client is constructed.

Four agents used to build their own `AsyncOpenAI`, which meant a base URL added
for a second provider would have had four places to be forgotten in. The key
never leaves this process and is never logged: only whether one is configured.
"""

from __future__ import annotations

from agents import AsyncOpenAI

from app.core.config import Settings


def build_client(cfg: Settings, *, no_retries: bool = False) -> AsyncOpenAI:
    """A client for whatever OpenAI-compatible endpoint is configured."""

    return AsyncOpenAI(
        api_key=cfg.openai_api_key,
        timeout=cfg.openai_timeout_seconds,
        **({"base_url": cfg.openai_base_url} if cfg.openai_base_url else {}),
        **({"max_retries": 0} if no_retries else {}),
    )
