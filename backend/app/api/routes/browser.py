"""Visible-browser capture endpoints (v0.2).

    POST /api/browser/start                launch the headed browser
    GET  /api/browser/status               is it running, what is open
    GET  /api/browser/current-page         what would be captured right now
    POST /api/browser/capture-current-job  read that page into the job库
    POST /api/browser/stop                 close the browser

The human owns login, search, navigation and any verification. These endpoints
only read the page that is already open, and never press an apply or message
control. ``analyze`` defaults to false so capture never spends API credit.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.errors import ValidationError
from app.core.logging import get_logger, log_event
from app.db.session import get_db
from app.job_sources import get_browser_source
from app.job_sources.boss.extractor import (
    NotAJobPageError,
    PageEmptyError,
    VerificationRequiredError,
)
from app.schemas.analysis import AnalysisMeta, AnalysisResponse
from app.schemas.browser import (
    BrowserStartResponse,
    BrowserStatusResponse,
    CapturedFields,
    CaptureResponse,
    CurrentPageResponse,
)
from app.schemas.common import MessageResponse
from app.services import job_intake, job_matcher
from app.services.browser_session import (
    BrowserStatus,
    NoSupportedPageError,
    canonical_url,
    get_browser_session,
    redact_url,
    site_for_url,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/api/browser", tags=["browser"])


def _status_payload(status: BrowserStatus) -> dict:
    return {
        "running": status.running,
        "site": status.site,
        "current_url": status.current_url,
        "current_title": status.current_title,
        "page_count": status.page_count,
        "supported_hosts": status.supported_hosts or [],
        "profile_dir": status.profile_dir,
        "channel": status.channel,
        "message": status.message,
    }


# --------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------


@router.get("/status", response_model=BrowserStatusResponse)
async def browser_status() -> BrowserStatusResponse:
    """Polled by the UI every few seconds. Cheap and side-effect free."""
    status = await get_browser_session().status()
    return BrowserStatusResponse(**_status_payload(status))


@router.post("/start", response_model=BrowserStartResponse)
async def start_browser() -> BrowserStartResponse:
    """Launch the visible browser, or report that it is already up.

    Starting twice is not an error - the second call returns
    ``already_running=true`` and the current status.
    """
    status, already_running = await get_browser_session().start()
    payload = _status_payload(status)
    if already_running:
        payload["message"] = "浏览器已在运行中"
    return BrowserStartResponse(**payload, already_running=already_running)


@router.post("/stop", response_model=MessageResponse)
async def stop_browser() -> MessageResponse:
    was_running = await get_browser_session().stop()
    return MessageResponse(
        message="浏览器已关闭" if was_running else "浏览器本来就没有运行",
        detail={"was_running": was_running},
    )


@router.get("/current-page", response_model=CurrentPageResponse)
async def current_page() -> CurrentPageResponse:
    """Describe the page that 采集当前岗位 would read, without reading it."""
    session = get_browser_session()
    try:
        page = await session.active_page()
    except NoSupportedPageError as exc:
        return CurrentPageResponse(message=exc.message)

    url = page.url
    site = site_for_url(url, session.supported_hosts)
    source = get_browser_source(site) if site else None
    is_job = bool(source and source.supports_url(url))

    try:
        title = await page.title()
    except Exception:  # noqa: BLE001 - a closing page must not 500
        title = None

    return CurrentPageResponse(
        site=site,
        url=url,
        title=title,
        is_job_page=is_job,
        external_id=source.external_id_for(url) if source else None,
        message="" if is_job else "当前页面未识别到职位详情，请先在招聘网站打开一个具体岗位。",
    )


# --------------------------------------------------------------------------
# capture
# --------------------------------------------------------------------------


@router.post("/capture-current-job", response_model=CaptureResponse)
async def capture_current_job(
    analyze: bool = Query(
        default=False,
        description="采集后立即用快速模型分析（默认关闭，避免自动消耗 API 额度）",
    ),
    db: Session = Depends(get_db),
) -> CaptureResponse:
    """Read the open job page and save it through the normal intake pipeline."""
    session = get_browser_session()
    page = await session.active_page()  # raises 409/422 with a Chinese message
    url = page.url

    site = site_for_url(url, session.supported_hosts)
    if site is None:
        raise ValidationError(
            "当前页面不是受支持的招聘网站。请在浏览器中打开 BOSS 直聘（zhipin.com）后重试。",
            detail={"url": url},
        )
    source = get_browser_source(site)

    if not source.supports_url(url):
        raise ValidationError(
            "当前页面未识别到职位详情，请先在招聘网站打开一个具体岗位。",
            detail={"url": url, "site": site},
        )

    try:
        posting, report = await source.capture_with_report(page)
    except VerificationRequiredError as exc:
        raise ValidationError(
            "招聘网站要求验证，请在浏览器中手动完成后重试。",
            detail={"site": site},
        ) from exc
    except PageEmptyError as exc:
        raise ValidationError(
            "招聘网站没有向自动化浏览器返回页面内容（页面为空白）。本工具不会绕过此类限制，请改用「岗位库 → 添加岗位」手动粘贴 JD。",
            detail={"site": site, "reason": "page_empty"},
        ) from exc
    except NotAJobPageError as exc:
        raise ValidationError(
            "当前页面未识别到职位详情，请先在招聘网站打开一个具体岗位。",
            detail={"site": site},
        ) from exc

    # Store the query-free URL: BOSS puts session tokens in the query string,
    # and a stable URL is also more useful for comparison later.
    posting.source_url = canonical_url(url)

    try:
        page_title = await page.title()
    except Exception:  # noqa: BLE001
        page_title = None

    outcome = job_intake.save_posting(
        db,
        posting,
        source=source,
        note=f"岗位已采集（{site} 浏览器采集）",
    )
    job = outcome.job

    log_event(
        logger,
        "browser.capture_completed",
        site=site,
        job_id=job.id,
        duplicate=outcome.duplicate,
        url=redact_url(url),
        jd_chars=report.description_chars,
    )

    analysis_response: AnalysisResponse | None = None
    if analyze:
        result = await job_matcher.analyze_job(db, job.id)
        analysis_response = AnalysisResponse(
            meta=AnalysisMeta(
                analysis_id=result.analysis.id,
                job_id=result.analysis.job_id,
                resume_id=result.analysis.resume_id,
                model=result.analysis.model,
                prompt_version=result.analysis.prompt_version,
                cache_key=result.analysis.cache_key,
                cached=result.cached,
                created_at=result.analysis.created_at,
            ),
            result=result.result,
            pre_analysis=result.pre_analysis,
        )

    # Imported here to reuse the exact serialisation the job routes use.
    from app.api.routes.jobs import _to_detail

    return CaptureResponse(
        job_id=job.id,
        duplicate=outcome.duplicate,
        site=site,
        selected_url=posting.source_url,
        page_title=page_title,
        job=_to_detail(job),
        fields=CapturedFields(
            title=posting.title,
            company=posting.company or None,
            city=posting.city,
            salary_text=posting.salary_text,
            experience_text=posting.experience_text,
            education_text=posting.education_text,
            external_id=posting.external_id,
            description_chars=report.description_chars,
            fields_found=report.fields_found,
            fields_missing=report.fields_missing,
            extra=posting.extra,
        ),
        analysis=analysis_response,
        message=(
            f"该岗位已存在（{job.company} · {job.title}）"
            if outcome.duplicate
            else f"采集成功：{job.company} · {job.title}"
        ),
    )
