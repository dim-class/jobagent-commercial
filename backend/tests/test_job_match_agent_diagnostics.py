"""`job_match_agent.run_job_match`'s failure path: classification + logging.

Patches `Runner.run` directly (never a real OpenAI call) with a real SDK
exception carrying canary/private-looking `code`/`request_id` values, the
same adversarial shape reproduced against this repo, and asserts neither the
raised `UpstreamError` nor the structured log line it emits ever contains
the canary - only the safe, allow-listed classification fields.
"""

from __future__ import annotations

import io
import logging

import httpx
import openai
import pytest

from app.agents.job_match_agent import run_job_match
from app.core.config import Settings
from app.core.errors import UpstreamError

CANARY_CODE = "SENSITIVE_CODE_CANARY_agent"
CANARY_REQUEST_ID = "SENSITIVE_REQID_CANARY_agent"


def _canary_rate_limit_error() -> openai.RateLimitError:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(
        429,
        request=request,
        json={"error": {"message": "irrelevant private detail", "code": CANARY_CODE}},
        headers={"x-request-id": CANARY_REQUEST_ID},
    )
    return openai.RateLimitError(
        "Error code: 429 - irrelevant private detail",
        response=response,
        body={"code": CANARY_CODE},
    )


async def test_upstream_failure_never_leaks_canary_metadata_in_error_or_log(monkeypatch):
    async def _boom(*args, **kwargs):
        raise _canary_rate_limit_error()

    monkeypatch.setattr("app.agents.job_match_agent.Runner.run", _boom)
    settings = Settings(openai_api_key="sk-test-not-a-real-key")

    root = logging.getLogger()
    assert root.handlers, "no handler installed on the root logger"
    handler = root.handlers[0]
    buffer = io.StringIO()
    original_stream = handler.stream
    handler.stream = buffer
    # Tests run at LOG_LEVEL=WARNING; the event this test checks logs at the
    # default INFO level, so raise the root level just enough to observe it.
    original_level = root.level
    root.setLevel(logging.INFO)
    try:
        with pytest.raises(UpstreamError) as excinfo:
            await run_job_match(
                model_name="gpt-test",
                resume_profile={},
                resume_excerpt="",
                strategy={},
                job={"normalized_description": "irrelevant JD text"},
                pre_analysis={},
                settings=settings,
            )
    finally:
        handler.stream = original_stream
        root.setLevel(original_level)

    exc = excinfo.value
    assert CANARY_CODE not in exc.message
    assert CANARY_REQUEST_ID not in exc.message
    assert CANARY_CODE not in str(exc.detail)
    assert CANARY_REQUEST_ID not in str(exc.detail)
    assert exc.detail["category"] == "rate_limit_exceeded"
    assert exc.detail["error_code"] is None
    assert exc.detail["request_id"] is None

    log_text = buffer.getvalue()
    assert CANARY_CODE not in log_text
    assert CANARY_REQUEST_ID not in log_text
    assert "analysis.upstream_failed" in log_text
    assert "rate_limit_exceeded" in log_text
