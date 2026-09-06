"""Chrome-extension endpoints (POC).

    POST /api/extension/jobs/preview   what would happen - writes nothing
    POST /api/extension/jobs/import    one explicit save, confirmed=true

The extension reads a page the human already opened and hands over the fields
it found. This module never fetches a URL, never contacts a recruitment site,
and never saves anything the user did not click to save.

**Loopback only.** Both endpoints refuse a request whose peer is not a loopback
address. The server already binds to 127.0.0.1, so this is belt and braces -
but it makes the boundary an assertion rather than a deployment assumption.
"""

from __future__ import annotations

import ipaddress
import json

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.logging import get_logger, log_event
from app.db.session import get_db
from app.schemas.extension import (
    KnownJobCard,
    KnownJobsRequest,
    KnownJobsResponse,
    ImportRequest,
    ImportResponse,
    PreviewRequest,
    PreviewResponse,
    PreviewRow,
)
from app.models import Job
from app.models.enums import JobSourceName
from app.services import extension_intake
from app.services.urls import boss_external_id, canonical_url
from app.services.salary_ocr import recognize_salary, MAX_IMAGE_BYTES
from starlette.concurrency import run_in_threadpool

logger = get_logger(__name__)
router = APIRouter(prefix="/api/extension", tags=["extension"])


@router.post("/salary-ocr")
async def salary_ocr(request: Request) -> dict:
    """Only an in-memory salary crop, bounded before JSON decoding; never persist it."""
    require_loopback(request)
    # Reject web-page callers. Chrome extension requests and local tools only.
    origin = request.headers.get("origin", "")
    if origin and not re_extension_origin(origin):
        raise ForbiddenError("薪资识别仅允许本机扩展调用。")
    from app.core.errors import ValidationError
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_IMAGE_BYTES * 4 // 3 + 1024:
            raise ValidationError("薪资截图过大。")
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict) or set(payload) != {"image"} or not isinstance(payload["image"], str):
            raise ValueError("invalid_payload")
        return await run_in_threadpool(recognize_salary, payload["image"])
    except (ValueError, TypeError):
        raise ValidationError("需要有效的小幅 PNG 薪资截图。") from None


def re_extension_origin(origin: str) -> bool:
    import re
    return re.fullmatch(r"chrome-extension://[a-p]{32}", origin) is not None

STATUS_LABEL = {
    "new": "新岗位",
    "duplicate": "已存在",
    "incomplete": "信息不足",
    "excluded": "已排除",
}


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


def require_loopback(request: Request) -> None:
    """Only a process on this machine may talk to the extension endpoints.

    A peer that parses as an IP address must be a loopback address. Anything
    that is *not* an IP address is not a remote caller at all - it is a
    non-TCP transport (or the test client), since the peer host on a real
    socket is always numeric.
    """
    host = (request.client.host if request.client else "") or ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return

    if not address.is_loopback:
        log_event(logger, "extension.rejected_remote_client", client=host)
        raise ForbiddenError("扩展接口只接受本机请求。", detail={"client": host})


@router.post("/jobs/known", response_model=KnownJobsResponse)
def known_jobs(
    request: Request,
    payload: KnownJobsRequest = Body(...),
    db: Session = Depends(get_db),
) -> KnownJobsResponse:
    """Which of these postings the library already has. Writes nothing.

    A search run may only open a fixed number of job details, and that budget
    was being spent re-opening postings already stored: a repeat search of
    「云计算工程师」 attempted its whole 20-candidate allowance and imported
    zero. Answering from the URL alone lets the runner skip those *before*
    spending a slot - the `(source, external_id)` unique index already makes
    the URL sufficient, so no page needs to be opened to find out.

    Deliberately not filtered on data quality: a stored job whose salary is
    still missing counts as known here. Re-collecting it is what the salary
    backfill run is for, and letting it consume a search slot would put the
    search back to work it has already done.
    """
    require_loopback(request)

    external_ids: dict[str, str] = {}
    for raw in payload.urls:
        canonical = canonical_url(raw)
        if not canonical:
            continue
        external_id = boss_external_id(canonical)
        if external_id:
            external_ids[external_id] = canonical

    known: list[str] = []
    if external_ids:
        rows = db.scalars(
            select(Job.external_id).where(
                Job.source == JobSourceName.boss,
                Job.external_id.in_(list(external_ids)),
            )
        ).all()
        known = [external_ids[value] for value in rows if value in external_ids]

    by_card = _known_by_card_signature(db, payload.cards, set(known))
    known.extend(by_card)

    log_event(
        logger,
        "extension.known_checked",
        asked=len(payload.urls),
        known=len(known),
        by_signature=len(by_card),
    )
    return KnownJobsResponse(known=known)


def _signature(*values: str | None) -> tuple[str, ...] | None:
    """The card fields, whitespace-normalized. ``None`` when identity is thin.

    Company and title must both be present: without them there is no claim to
    make, and skipping on a partial match would refuse a posting the library
    has never seen.
    """

    cleaned = tuple("".join((value or "").split()) for value in values)
    return cleaned if cleaned[0] and cleaned[1] else None


def _salary_blind(signature: tuple[str, ...]) -> tuple[str, ...]:
    """The same signature without the salary.

    Used only when one side has no salary at all. BOSS obfuscates a great many
    card salaries with a private-use font, so the card reads empty while the
    stored job carries a figure the OCR or the salary backfill supplied later -
    409 of 632 jobs in the library that motivated this were created with no
    readable card salary. Requiring exact equality there sent the run to open
    the posting again, every run, forever.

    The cost is measured, not assumed: on that library the full key collided
    for 2 rows and this one for 7, so it trades five rows of ambiguity for the
    two-thirds of postings whose salary a card never shows.
    """

    return signature[:2] + signature[3:]


def _known_by_card_signature(
    db: Session,
    cards: list[KnownJobCard],
    already: set[str],
) -> list[str]:
    """Cards the library already holds under a *different* job id.

    BOSS re-lists the same posting under a new ``job_detail`` id, so the
    ``(source, external_id)`` check above says "new" and the run spends a
    candidate slot opening it - only for the content hash to report a duplicate
    and import nothing. Over one 15-task run that was 66 of 78 opened details.

    The claim is deliberately narrow: company, title, salary, experience *and*
    city must all agree, which is everything a card shows. On the library this
    was written against, that key collided for 2 rows in 581 - so a genuinely
    different posting is occasionally skipped, and the description (which the
    card does not carry) is never guessed at. A looser key on company+title
    alone would have collided for 18.
    """

    wanted: dict[tuple[str, ...], list[str]] = {}
    blind: dict[tuple[str, ...], list[str]] = {}
    for card in cards:
        canonical = canonical_url(card.url)
        if not canonical or canonical in already:
            continue
        signature = _signature(
            card.company, card.title, card.salary_text, card.experience_text, card.city
        )
        if not signature:
            continue
        if signature[2]:
            wanted.setdefault(signature, []).append(canonical)
        else:
            blind.setdefault(_salary_blind(signature), []).append(canonical)
    if not wanted and not blind:
        return []

    titles = {signature[1] for signature in list(wanted) + list(blind)}
    rows = db.execute(
        select(
            Job.company,
            Job.title,
            Job.salary_text,
            Job.experience_text,
            Job.city,
        ).where(Job.source == JobSourceName.boss)
    ).all()

    found: list[str] = []
    seen: set[str] = set()

    def take(urls: list[str]) -> None:
        for canonical in urls:
            if canonical not in seen:
                seen.add(canonical)
                found.append(canonical)

    for row in rows:
        signature = _signature(*row)
        if signature is None or signature[1] not in titles:
            continue
        take(wanted.get(signature, []))
        take(blind.get(_salary_blind(signature), []))
        # The mirror case: the stored job is the one with no salary (collected
        # before the backfill ran) and the card now shows one.
        if not signature[2]:
            for key, urls in wanted.items():
                if _salary_blind(key) == _salary_blind(signature):
                    take(urls)
    return found


@router.post("/jobs/preview", response_model=PreviewResponse)
def preview(
    request: Request,
    payload: PreviewRequest = Body(...),
    db: Session = Depends(get_db),
) -> PreviewResponse:
    """Say which candidates are new and which are already known.

    Read-only by construction: every candidate goes through the same
    normalization and hashing a save would use, and then nothing is written.
    """
    require_loopback(request)

    rows: list[PreviewRow] = []
    counts = {"new": 0, "duplicate": 0, "incomplete": 0, "excluded": 0}
    policy = extension_intake.early_career_policy_for_task(db, payload.task_id)

    for index, candidate in enumerate(payload.candidates):
        verdict = extension_intake.inspect(db, candidate, early_career_policy=policy)
        counts[verdict.status] += 1
        rows.append(
            PreviewRow(
                index=index,
                title=(candidate.title or "").strip()[:256] or "（无职位名称）",
                company=(candidate.company or "").strip()[:256],
                status=verdict.status,  # type: ignore[arg-type]
                status_label=STATUS_LABEL[verdict.status],
                existing_job_id=verdict.existing_job_id,
                blocking_fields=verdict.blocking_fields,
                enrichable_fields=verdict.enrichable_fields,
                warnings=verdict.warnings + list(candidate.warnings),
            )
        )

    warnings: list[str] = []
    if payload.page_type == "search" and counts["incomplete"]:
        warnings.append(
            "搜索结果卡片不包含职位描述，无法直接导入。"
            "请打开需要的职位详情页，再点一次「检测当前页面」。"
        )
    if payload.page_type == "unsupported":
        warnings.append("当前页面不是可识别的 BOSS 职位或搜索页面。")

    # Counts only. No titles, no companies, no URLs.
    log_event(
        logger,
        "extension.preview",
        page_type=payload.page_type,
        detected=len(payload.candidates),
        new=counts["new"],
        duplicate=counts["duplicate"],
        incomplete=counts["incomplete"],
    )

    return PreviewResponse(
        detected=len(payload.candidates),
        new_count=counts["new"],
        duplicate_count=counts["duplicate"],
        incomplete_count=counts["incomplete"],
        excluded_count=counts["excluded"],
        rows=rows,
        warnings=warnings,
        message="这是预览结果，什么都还没有保存。要保存请对具体岗位点「导入」。",
    )


@router.post("/jobs/import", response_model=ImportResponse)
def import_job(
    request: Request,
    payload: ImportRequest = Body(...),
    db: Session = Depends(get_db),
) -> ImportResponse:
    """Save one candidate. Explicit, one at a time, no bulk version.

    Reuses ``job_intake.save_posting`` - the same path a pasted JD, a Quick
    Capture confirmation and a browser capture all take. A duplicate is a
    normal outcome, not an error: the existing job id comes back.
    """
    require_loopback(request)

    if not payload.confirmed:
        from app.core.errors import ValidationError

        raise ValidationError(
            "需要明确确认后才能导入岗位。", detail={"field": "confirmed"}
        )

    policy = extension_intake.early_career_policy_for_task(db, payload.task_id)
    outcome = extension_intake.import_one(
        db, payload.candidate, early_career_policy=policy
    )
    job = outcome.job

    return ImportResponse(
        job_id=job.id,
        duplicate=outcome.duplicate,
        title=job.title,
        company=job.company,
        message=(
            f"该岗位已存在（{job.company or '未知公司'} · {job.title}）"
            if outcome.duplicate
            else f"已导入：{job.company or '未知公司'} · {job.title}"
        ),
    )
