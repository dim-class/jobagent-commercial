"""Safe classification of OpenAI SDK failures (M5a diagnostics delta).

Uses real OpenAI SDK exception classes (constructed locally, never a live
call) so the classifier is verified against the actual attribute surface
those exceptions expose, not a guess. Several tests are adversarial: they
attach canary/private-looking strings to fields the SDK genuinely exposes
(``code``/``request_id``) to prove the classifier discards anything outside
its allow-list rather than passing it through.
"""

from __future__ import annotations

import httpx
import openai
import pytest

from app.services.ai_diagnostics import CATEGORIES, classify_openai_error

CANARY = "SENSITIVE_CANARY_DO_NOT_LEAK_9f8e7d6c5b4a"


def _status_error(cls, *, status_code: int, code: str | None, request_id: str = "req_abc123def456"):
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(
        status_code,
        request=request,
        json={"error": {"message": "some private detail", "code": code}},
        headers={"x-request-id": request_id},
    )
    return cls(
        "Error code: %d - some private detail" % status_code,
        response=response,
        body={"message": "some private detail", "code": code},
    )


def test_authentication_error_is_classified_with_safe_fields():
    exc = _status_error(openai.AuthenticationError, status_code=401, code="invalid_api_key")
    result = classify_openai_error(exc)
    assert result.category == "authentication"
    assert result.http_status == 401
    assert result.error_code == "invalid_api_key"
    assert result.request_id == "req_abc123def456"
    assert "some private detail" not in result.message


@pytest.mark.parametrize("cls", [openai.PermissionDeniedError, openai.NotFoundError])
def test_permission_and_not_found_map_to_permission_category(cls):
    exc = _status_error(cls, status_code=403, code="model_not_found")
    result = classify_openai_error(exc)
    assert result.category == "permission"


def test_insufficient_quota_is_distinguished_from_rate_limit_exceeded():
    quota_exc = _status_error(openai.RateLimitError, status_code=429, code="insufficient_quota")
    quota_result = classify_openai_error(quota_exc)
    assert quota_result.category == "insufficient_quota"
    assert quota_result.http_status == 429

    rate_exc = _status_error(openai.RateLimitError, status_code=429, code="rate_limit_exceeded")
    rate_result = classify_openai_error(rate_exc)
    assert rate_result.category == "rate_limit_exceeded"
    assert rate_result.http_status == 429

    assert quota_result.message != rate_result.message


def test_rate_limit_error_with_unrecognized_code_still_falls_back_safely():
    exc = _status_error(openai.RateLimitError, status_code=429, code=CANARY)
    result = classify_openai_error(exc)
    assert result.category == "rate_limit_exceeded"
    assert result.error_code is None
    assert CANARY not in (result.message or "")


@pytest.mark.parametrize("cls", [openai.BadRequestError, openai.UnprocessableEntityError])
def test_bad_request_and_unprocessable_map_to_invalid_request(cls):
    exc = _status_error(cls, status_code=400, code="invalid_value")
    result = classify_openai_error(exc)
    assert result.category == "invalid_request"


def test_api_timeout_error_is_classified_as_timeout_or_network():
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    exc = openai.APITimeoutError(request=request)
    result = classify_openai_error(exc)
    assert result.category == "timeout_or_network"
    assert result.http_status is None
    assert result.request_id is None


def test_api_connection_error_is_classified_as_timeout_or_network():
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    exc = openai.APIConnectionError(request=request)
    result = classify_openai_error(exc)
    assert result.category == "timeout_or_network"


def test_unrecognized_exception_is_classified_unknown_and_never_raises():
    exc = RuntimeError("some raw internal detail that must never reach the UI")
    result = classify_openai_error(exc)
    assert result.category == "unknown"
    assert "raw internal detail" not in result.message


def test_unrecognized_exception_metadata_is_never_inspected_even_if_present():
    """A non-SDK exception carrying same-named attributes (however they got
    there) must not have them surfaced - only recognized SDK exception types
    are ever read from."""

    class SpoofedException(Exception):
        status_code = 401
        code = "invalid_api_key"
        request_id = "req_abc123def456"

    result = classify_openai_error(SpoofedException("spoofed"))
    assert result.category == "unknown"
    assert result.http_status is None
    assert result.error_code is None
    assert result.request_id is None


def test_canary_error_code_and_request_id_are_discarded_not_passed_through():
    exc = _status_error(
        openai.AuthenticationError,
        status_code=401,
        code=CANARY,
        request_id=CANARY,
    )
    result = classify_openai_error(exc)
    assert result.category == "authentication"
    assert result.error_code is None
    assert result.request_id is None
    assert CANARY not in (result.message or "")
    assert CANARY != result.error_code
    assert CANARY != result.request_id


def test_out_of_range_http_status_is_discarded():
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(
        200,  # httpx requires a valid status to construct a Response
        request=request,
        json={"error": {"message": "irrelevant"}},
    )
    exc = openai.APIStatusError("boom", response=response, body=None)
    # Force an out-of-range value the way a malformed upstream might.
    exc.status_code = 99999
    result = classify_openai_error(exc)
    assert result.http_status is None


def test_every_category_message_is_generic_and_never_derived_from_the_exception():
    """The message is a fixed, per-category string - never `str(exc)` - so a
    prompt/response fragment inside the exception's own text can never reach
    the log line or the UI through this path."""
    exc = _status_error(openai.AuthenticationError, status_code=401, code="invalid_api_key")
    result = classify_openai_error(exc)
    assert result.message not in str(exc)
    assert result.category in CATEGORIES
