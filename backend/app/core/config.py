"""Application configuration.

All settings come from environment variables (optionally via the project-root
``.env`` file). The OpenAI API key lives here and **only** here - it is never
serialised into a response, written to the database, or logged.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.paths import (
    BROWSER_PROFILES_DIR,
    CAREER_STRATEGY_PATH,
    DATA_DIR,
    ENV_FILE_PATH,
    FRONTEND_DIST_DIR,
    PROJECT_ROOT,
    resolve_relative,
)


class Settings(BaseSettings):
    """Runtime configuration, read once and cached."""

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE_PATH),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- OpenAI -----------------------------------------------------------
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    #: Where to send the calls. Empty means OpenAI itself. Any OpenAI-compatible
    #: endpoint works here (DeepSeek, Moonshot, 通义 and most domestic providers
    #: expose one), which is the whole of this project's "other providers"
    #: support - it speaks one protocol, not several SDKs.
    openai_base_url: str | None = Field(default=None, alias="OPENAI_BASE_URL")
    openai_model_fast: str = Field(default="gpt-5.6-luna", alias="OPENAI_MODEL_FAST")
    openai_model_smart: str = Field(default="gpt-5.6-terra", alias="OPENAI_MODEL_SMART")
    openai_timeout_seconds: float = Field(default=120.0, alias="OPENAI_TIMEOUT_SECONDS")

    # --- storage ----------------------------------------------------------
    database_url: str = Field(
        default=f"sqlite:///{(DATA_DIR / 'jobagent.db').as_posix()}", alias="DATABASE_URL"
    )

    # --- behaviour --------------------------------------------------------
    max_analyses_per_run: int = Field(default=50, alias="MAX_ANALYSES_PER_RUN")
    auto_apply: bool = Field(default=False, alias="AUTO_APPLY")
    # M6 exposes only the per-job confirmation workflow.  This is not an
    # authority to execute: a still-valid ApplicationApproval remains the
    # sole authority for one attempt.
    human_confirmed_apply_enabled: bool = Field(
        default=False, alias="HUMAN_CONFIRMED_APPLY_ENABLED"
    )

    # --- server -----------------------------------------------------------
    app_host: str = Field(default="127.0.0.1", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    serve_frontend: bool = Field(default=False, alias="JOBAGENT_SERVE_FRONTEND")
    frontend_dist_dir: str = Field(
        default=str(FRONTEND_DIST_DIR), alias="JOBAGENT_FRONTEND_DIR"
    )
    cors_origins: str = Field(
        default="http://127.0.0.1:5173,http://localhost:5173",
        alias="CORS_ORIGINS",
    )

    #: Origins allowed by pattern rather than by exact string.
    #:
    #: An unpacked Chrome extension gets a machine-specific 32-character id, so
    #: its origin cannot be listed literally. The pattern matches the shape of a
    #: Chrome extension origin and nothing else; the extension endpoints
    #: additionally refuse any peer that is not a loopback address, so this
    #: widens CORS without widening who can actually reach the server.
    cors_origin_regex: str = Field(
        default=r"^chrome-extension://[a-p]{32}$",
        alias="CORS_ORIGIN_REGEX",
    )

    # --- browser capture (v0.2) -------------------------------------------
    # The recruitment browser is ALWAYS headed: the human logs in, searches,
    # navigates and clears any verification. There is deliberately no headless
    # switch for it - see CLAUDE.md.
    browser_profile_dir: str = Field(
        default=str(BROWSER_PROFILES_DIR / "boss"), alias="BROWSER_PROFILE_DIR"
    )
    browser_supported_hosts: str = Field(
        default="zhipin.com", alias="BROWSER_SUPPORTED_HOSTS"
    )
    browser_locale: str = Field(default="zh-CN", alias="BROWSER_LOCALE")
    # "auto" tries Playwright Chromium, then system Edge, then system Chrome.
    # Force one with "chromium" | "msedge" | "chrome".
    browser_channel: str = Field(default="auto", alias="BROWSER_CHANNEL")
    browser_launch_timeout_ms: int = Field(default=60_000, alias="BROWSER_LAUNCH_TIMEOUT_MS")

    # --- quick capture (v0.3) ---------------------------------------------
    # Content the human pastes/uploads. The backend never fetches a URL and
    # never contacts a recruitment site - see CLAUDE.md.
    quick_capture_ai_extraction: bool = Field(
        default=True, alias="QUICK_CAPTURE_AI_EXTRACTION"
    )
    quick_capture_max_image_mb: int = Field(default=10, alias="QUICK_CAPTURE_MAX_IMAGE_MB")

    # --- application queue (v0.4) ------------------------------------------
    # Informational only - the UI shows progress towards it and never blocks.
    daily_application_target: int = Field(default=10, alias="DAILY_APPLICATION_TARGET")
    # Daily metrics are reported in this timezone, not naive UTC.
    report_timezone: str = Field(default="Asia/Tokyo", alias="REPORT_TIMEZONE")

    # --- career analytics (v0.6) -------------------------------------------
    # A job applied to this morning has not "failed to get a reply" - it has
    # not had time. Applications younger than this are excluded from the
    # mature denominators unless a reply already arrived.
    response_maturity_days: int = Field(default=7, alias="RESPONSE_MATURITY_DAYS")
    interview_maturity_days: int = Field(default=14, alias="INTERVIEW_MATURITY_DAYS")
    #: Below this a cohort's rate is shown but never compared or recommended on.
    analytics_min_sample: int = Field(default=5, alias="ANALYTICS_MIN_SAMPLE")
    #: A strategy recommendation needs at least this many mature applications.
    analytics_recommend_sample: int = Field(default=8, alias="ANALYTICS_RECOMMEND_SAMPLE")

    #: Below this share of weighted coverage, an offer comparison reports
    #: scores but names no winner. A confident ranking built on a third of
    #: the inputs is worse than no ranking at all.
    offer_decision_min_coverage: float = Field(
        default=0.7, ge=0.0, le=1.0, alias="OFFER_DECISION_MIN_COVERAGE"
    )

    # --- prompts ----------------------------------------------------------
    # Bump this whenever the JobMatchAgent prompt changes; it is part of the
    # analysis cache key so old cached results are transparently invalidated.
    prompt_version: str = Field(default="v1", alias="PROMPT_VERSION")

    career_strategy_path: str = Field(
        default=str(CAREER_STRATEGY_PATH), alias="CAREER_STRATEGY_PATH"
    )

    @field_validator("openai_api_key", mode="before")
    @classmethod
    def _blank_key_is_none(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @property
    def auto_apply_enabled(self) -> bool:
        """There is no global automatic-application mode.

        M6 uses ``human_confirmed_apply_enabled`` plus a separate, per-job
        approval.  It never changes this legacy invariant.
        """
        return False

    @property
    def openai_configured(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def quick_capture_max_image_bytes(self) -> int:
        return max(1, self.quick_capture_max_image_mb) * 1024 * 1024

    @property
    def browser_profile_path(self) -> Path:
        return resolve_relative(self.browser_profile_dir)

    @property
    def browser_supported_host_list(self) -> list[str]:
        """Registrable domains the capture flow will read from."""
        return [h.strip().lower() for h in self.browser_supported_hosts.split(",") if h.strip()]

    @property
    def strategy_file(self) -> Path:
        return resolve_relative(self.career_strategy_path)

    @property
    def frontend_dist_path(self) -> Path:
        return resolve_relative(self.frontend_dist_dir)

    @property
    def sqlalchemy_url(self) -> str:
        """Absolute SQLAlchemy URL (relative sqlite paths anchor at the repo root)."""
        url = self.database_url
        prefix = "sqlite:///"
        if url.startswith(prefix):
            raw = url[len(prefix) :]
            if raw.startswith("/"):  # sqlite:////abs/path
                return url
            abs_path = resolve_relative(raw)
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            return f"{prefix}{abs_path.as_posix()}"
        return url

    def safe_dump(self) -> dict[str, object]:
        """Config snapshot that is safe to log or return over HTTP."""
        return {
            "openai_configured": self.openai_configured,
            "model_fast": self.openai_model_fast,
            "model_smart": self.openai_model_smart,
            "database_url": self.database_url,
            "max_analyses_per_run": self.max_analyses_per_run,
            "auto_apply": self.auto_apply_enabled,
            "prompt_version": self.prompt_version,
            "app_host": self.app_host,
            "app_port": self.app_port,
            "serve_frontend": self.serve_frontend,
            "browser_profile_dir": self.browser_profile_dir,
            "browser_supported_hosts": self.browser_supported_hosts,
            "browser_channel": self.browser_channel,
            "quick_capture_ai_extraction": self.quick_capture_ai_extraction,
            "quick_capture_max_image_mb": self.quick_capture_max_image_mb,
            "daily_application_target": self.daily_application_target,
            "report_timezone": self.report_timezone,
            "response_maturity_days": self.response_maturity_days,
            "analytics_min_sample": self.analytics_min_sample,
            "analytics_recommend_sample": self.analytics_recommend_sample,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
