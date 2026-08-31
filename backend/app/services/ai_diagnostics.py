"""Safe classification of an OpenAI SDK failure (M5a diagnostics delta).

Every field this module produces is deliberately narrow: a category, an HTTP
status code, a short error code, and a request id - never the API key, never
a prompt/resume/JD excerpt, never a raw response body, header, or cookie.
This module never touches ``exc.response``, ``exc.body``, or ``str(exc)``
itself, since any of those could echo request/response content back into a
log line or the UI.

That alone is not enough: the plain attributes the SDK exposes
(``status_code``/``code``/``request_id``) are themselves untrusted values
that ultimately come from the network response, so a compromised/misbehaving
upstream could stuff private-looking text into any of them. Every one of
those three fields is therefore validated - a known-shape HTTP status, a
``code`` drawn from a fixed allow-list, a ``request_id`` matching the SDK's
own ``req_...`` shape - and anything that doesn't validate is discarded
(``None``), never passed through or logged. Metadata is read only from
exception types this module actually recognizes; an unrecognized exception
(``category == "unknown"``) never has its attributes inspected at all, since
nothing guarantees a non-SDK exception's same-named attributes mean the same
thing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import openai

#: Chinese, actionable, and generic on purpose - never derived from the
#: exception's own message text (which could echo request content).
_CATEGORY_MESSAGE = {
    "authentication": "OpenAI 鉴权失败，请检查 API Key 是否正确、未过期。",
    "permission": "没有访问该模型的权限，请检查账户权限或模型名称配置。",
    "insufficient_quota": "OpenAI 账户余额或配额已用尽，请检查账户余量或升级套餐后重试。",
    "rate_limit_exceeded": "已达到 OpenAI 请求速率限制，请稍后重试。",
    "timeout_or_network": "连接 OpenAI 超时或网络异常，请检查网络后重试。",
    "invalid_request": "请求参数不被 OpenAI 接受，请检查模型名称与请求内容配置。",
    "unknown": "调用 OpenAI 失败，原因未知，请稍后重试或联系管理员。",
}

CATEGORIES = tuple(_CATEGORY_MESSAGE)

#: Fixed allow-list of short SDK error codes this module will ever surface.
#: Anything else - including a canary/secret-shaped string a misbehaving
#: upstream stuffed into the field - is silently discarded, never passed
#: through or logged.
_KNOWN_ERROR_CODES = frozenset(
    {
        "invalid_api_key",
        "invalid_organization",
        "account_deactivated",
        "model_not_found",
        "insufficient_permissions",
        "unsupported_country_region_territory",
        "insufficient_quota",
        "billing_hard_limit_reached",
        "rate_limit_exceeded",
        "requests_rate_limit_exceeded",
        "tokens_rate_limit_exceeded",
        "invalid_value",
        "invalid_type",
        "missing_required_parameter",
        "context_length_exceeded",
        "string_above_max_length",
    }
)

#: The SDK's own request-id shape (e.g. ``req_abc123``) - anything else is
#: discarded rather than trusted as a safe, opaque identifier.
_REQUEST_ID_RE = re.compile(r"^req_[A-Za-z0-9]{6,64}$")

_MIN_HTTP_STATUS = 100
_MAX_HTTP_STATUS = 599


@dataclass(slots=True, frozen=True)
class ErrorClassification:
    category: str
    message: str
    http_status: int | None = None
    error_code: str | None = None
    request_id: str | None = None


def _safe_http_status(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not (_MIN_HTTP_STATUS <= value <= _MAX_HTTP_STATUS):
        return None
    return value


def _safe_error_code(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value if value in _KNOWN_ERROR_CODES else None


def _safe_request_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value if _REQUEST_ID_RE.fullmatch(value) else None


def _category_for(exc: Exception, *, raw_code: object) -> str:
    # Order matters: subclasses before their bases (`OAuthError` before
    # `AuthenticationError`, `APITimeoutError` before `APIConnectionError`).
    if isinstance(exc, openai.AuthenticationError):
        return "authentication"
    if isinstance(exc, (openai.PermissionDeniedError, openai.NotFoundError)):
        return "permission"
    if isinstance(exc, openai.RateLimitError):
        # Both a quota exhaustion and an ordinary rate limit are HTTP 429;
        # only a recognized `code` distinguishes them. An unrecognized code
        # (including a canary/private string) falls back to the more
        # general, still-accurate "rate_limit_exceeded" bucket rather than
        # ever echoing the raw value anywhere.
        if raw_code == "insufficient_quota":
            return "insufficient_quota"
        return "rate_limit_exceeded"
    if isinstance(exc, openai.APITimeoutError):
        return "timeout_or_network"
    if isinstance(exc, openai.APIConnectionError):
        return "timeout_or_network"
    if isinstance(exc, (openai.BadRequestError, openai.UnprocessableEntityError)):
        return "invalid_request"
    return "unknown"


def classify_openai_error(exc: Exception) -> ErrorClassification:
    """Turn any exception from an OpenAI SDK call into a safe, typed summary.

    Never raises - an exception this function cannot recognize (including a
    non-OpenAI exception) is classified ``"unknown"`` and none of its
    attributes are inspected, since nothing guarantees a same-named attribute
    on an arbitrary exception carries the same meaning (or safety) as the
    SDK's own.
    """
    # Only ever read these attributes off an exception type this module
    # itself recognizes - never off an arbitrary/unknown exception.
    is_recognized = isinstance(exc, openai.OpenAIError)
    raw_status = getattr(exc, "status_code", None) if is_recognized else None
    raw_code = getattr(exc, "code", None) if is_recognized else None
    raw_request_id = getattr(exc, "request_id", None) if is_recognized else None

    category = _category_for(exc, raw_code=raw_code) if is_recognized else "unknown"
    return ErrorClassification(
        category=category,
        message=_CATEGORY_MESSAGE[category],
        http_status=_safe_http_status(raw_status),
        error_code=_safe_error_code(raw_code),
        request_id=_safe_request_id(raw_request_id),
    )
