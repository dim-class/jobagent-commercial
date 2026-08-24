"""FastAPI application entry point.

Run locally with:
    uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.routes import api_router
from app.core.career_strategy import load_strategy
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger, log_event, setup_logging
from app.core.paths import ensure_runtime_dirs
from app.db.session import init_db

settings = get_settings()
setup_logging(settings.log_level)
logger = get_logger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_runtime_dirs()
    init_db()
    try:
        strategy = load_strategy(force=True)
        cities = len(strategy.get("target_cities") or [])
    except Exception as exc:  # noqa: BLE001 - a bad YAML must not block startup
        cities = 0
        log_event(logger, "startup.strategy_load_failed", error=type(exc).__name__)

    log_event(
        logger,
        "api.startup",
        version=__version__,
        host=settings.app_host,
        port=settings.app_port,
        openai_configured=settings.openai_configured,
        model_fast=settings.openai_model_fast,
        model_smart=settings.openai_model_smart,
        auto_apply=settings.auto_apply_enabled,
        strategy_cities=cities,
    )
    if not settings.openai_configured:
        logger.warning(
            "OPENAI_API_KEY 未配置：应用可正常浏览，AI 分析接口会返回配置错误（503）。"
        )
    yield

    # Close the capture browser so Playwright does not outlive the process.
    from app.services.browser_session import get_browser_session

    try:
        await get_browser_session().shutdown()
    except Exception as exc:  # noqa: BLE001 - shutdown must not raise
        log_event(logger, "browser.shutdown_failed", error=type(exc).__name__)
    log_event(logger, "api.shutdown")


app = FastAPI(
    title="AI Job Agent",
    description=(
        "本地优先的 AI 求职助手 v0.1 —— 简历解析 / 岗位录入 / AI 匹配分析 / 招呼语生成。\n\n"
        "安全策略：AUTO_APPLY 恒为 false，不做自动投递、不做自动打招呼、"
        "不绕过任何验证码或反爬机制。"
    ),
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    # The Chrome extension's origin carries a per-machine id, so it is matched
    # by shape. Credentials stay off: nothing here is cookie-authenticated.
    allow_origin_regex=settings.cors_origin_regex or None,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    log_event(
        logger,
        "api.error",
        path=request.url.path,
        code=exc.code,
        status=exc.status_code,
    )
    return JSONResponse(status_code=exc.status_code, content=exc.to_payload())


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    log_event(logger, "api.validation_error", path=request.url.path)
    # Pydantic puts the original exception object into ``ctx`` when a custom
    # validator raises, and that is not JSON-serializable - encoding it here
    # keeps a failed validation a 422 instead of turning it into a 500.
    errors = jsonable_encoder(exc.errors()[:10])
    return JSONResponse(
        status_code=422,
        content={
            "code": "validation_error",
            "message": "请求参数不合法",
            "detail": {"errors": errors},
        },
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "code": "internal_error",
            "message": "服务器内部错误，请查看后端日志",
            "detail": {"error_type": type(exc).__name__},
        },
    )


app.include_router(api_router)


@app.get("/", tags=["health"])
def root() -> dict[str, object]:
    return {
        "name": "AI Job Agent",
        "version": __version__,
        "docs": "/docs",
        "health": "/health",
        "auto_apply": settings.auto_apply_enabled,
    }
