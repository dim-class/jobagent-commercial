"""JobImportAgent - structured extraction from user-supplied content (v0.3).

Two entry points, one typed output (:class:`AIExtractedJob`):

    run_text_extraction(text)      the user pasted page text
    run_vision_extraction(image)   the user uploaded a screenshot

This agent only ever sees content the human explicitly handed over. It does not
fetch URLs and has no tools - it cannot reach the network on its own.

It is completely separate from ``JobMatchAgent``: extraction and matching are
two different AI calls, and Quick Capture never triggers matching.
"""

from __future__ import annotations

import base64
import time

from agents import Agent, AsyncOpenAI, OpenAIResponsesModel, RunConfig, Runner

from app.agents.import_prompts import (
    IMPORT_PROMPT_VERSION,
    TEXT_SYSTEM_PROMPT,
    VISION_SYSTEM_PROMPT,
    VISION_USER_PROMPT,
    build_text_user_prompt,
)
from app.agents.job_match_agent import require_openai
from app.core.config import Settings, get_settings
from app.core.errors import UpstreamError
from app.core.logging import get_logger, log_event
from app.schemas.quick_capture import AIExtractedJob

logger = get_logger(__name__)

AGENT_NAME = "JobImportAgent"

#: Extraction is a cheap, mechanical job - always the fast/bulk model.
MAX_TEXT_CHARS = 20_000


def _build_agent(model_name: str, instructions: str, cfg: Settings) -> Agent:
    client = AsyncOpenAI(api_key=cfg.openai_api_key, timeout=cfg.openai_timeout_seconds)
    return Agent(
        name=AGENT_NAME,
        instructions=instructions,
        model=OpenAIResponsesModel(model=model_name, openai_client=client),
        output_type=AIExtractedJob,
    )


async def _run(agent: Agent, prompt_input, *, model_name: str, kind: str) -> AIExtractedJob:
    started = time.perf_counter()
    try:
        result = await Runner.run(
            agent,
            prompt_input,
            max_turns=1,
            run_config=RunConfig(tracing_disabled=True, workflow_name="job-import"),
        )
    except Exception as exc:  # noqa: BLE001 - normalised for the UI
        log_event(
            logger,
            "import.upstream_failed",
            kind=kind,
            model=model_name,
            error=type(exc).__name__,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )
        raise UpstreamError(
            "AI提取服务暂时不可用，请使用文字粘贴或手动补充字段。",
            detail={"model": model_name, "error_type": type(exc).__name__, "kind": kind},
        ) from exc

    usage = getattr(getattr(result, "context_wrapper", None), "usage", None)
    log_event(
        logger,
        "import.model_returned",
        kind=kind,
        model=model_name,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
    )

    output = result.final_output_as(AIExtractedJob, raise_if_incorrect_type=False)
    if not isinstance(output, AIExtractedJob):
        raise UpstreamError(
            "AI提取返回的格式不正确，请重试或改用文字粘贴。",
            detail={"model": model_name, "kind": kind},
        )
    return output


async def run_text_extraction(
    text: str, *, settings: Settings | None = None
) -> tuple[AIExtractedJob, str]:
    """Extract a job from pasted page text. Returns (result, model_name)."""
    cfg = require_openai(settings or get_settings())
    model_name = cfg.openai_model_fast
    agent = _build_agent(model_name, TEXT_SYSTEM_PROMPT, cfg)
    result = await _run(
        agent,
        build_text_user_prompt((text or "")[:MAX_TEXT_CHARS]),
        model_name=model_name,
        kind="text",
    )
    return result, model_name


async def run_vision_extraction(
    image_bytes: bytes, mime_type: str, *, settings: Settings | None = None
) -> tuple[AIExtractedJob, str]:
    """Extract a job from an uploaded screenshot. Returns (result, model_name).

    The image is passed inline as a data URL and is never written to disk.
    """
    cfg = require_openai(settings or get_settings())
    model_name = cfg.openai_model_fast
    agent = _build_agent(model_name, VISION_SYSTEM_PROMPT, cfg)

    encoded = base64.b64encode(image_bytes).decode("ascii")
    prompt_input = [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": VISION_USER_PROMPT},
                {
                    "type": "input_image",
                    "detail": "auto",
                    "image_url": f"data:{mime_type};base64,{encoded}",
                },
            ],
        }
    ]
    result = await _run(agent, prompt_input, model_name=model_name, kind="vision")
    return result, model_name


__all__ = [
    "IMPORT_PROMPT_VERSION",
    "run_text_extraction",
    "run_vision_extraction",
]
