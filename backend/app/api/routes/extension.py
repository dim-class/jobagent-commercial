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

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.logging import get_logger, log_event
from app.db.session import get_db
from app.schemas.extension import (
    ImportRequest,
    ImportResponse,
    PreviewRequest,
    PreviewResponse,
    PreviewRow,
)
from app.services import extension_intake

logger = get_logger(__name__)
router = APIRouter(prefix="/api/extension", tags=["extension"])

STATUS_LABEL = {
    "new": "新岗位",
    "duplicate": "已存在",
    "incomplete": "信息不足",
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
    counts = {"new": 0, "duplicate": 0, "incomplete": 0}

    for index, candidate in enumerate(payload.candidates):
        verdict = extension_intake.inspect(db, candidate)
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

    outcome = extension_intake.import_one(db, payload.candidate)
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
