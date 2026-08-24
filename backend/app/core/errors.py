"""Domain exceptions mapped to clean HTTP responses in ``app.main``."""

from __future__ import annotations


class AppError(Exception):
    """Base class for expected, user-facing failures."""

    status_code = 400
    code = "app_error"

    def __init__(self, message: str, *, detail: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}

    def to_payload(self) -> dict:
        return {"code": self.code, "message": self.message, "detail": self.detail}


class ConfigurationError(AppError):
    """Something the operator must fix (typically a missing OPENAI_API_KEY)."""

    status_code = 503
    code = "configuration_error"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class DuplicateError(AppError):
    status_code = 409
    code = "duplicate"


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"


class UnsupportedFileType(AppError):
    status_code = 415
    code = "unsupported_file_type"


class UpstreamError(AppError):
    """The OpenAI call failed - surfaced as 502 so the UI can offer a retry."""

    status_code = 502
    code = "upstream_error"
