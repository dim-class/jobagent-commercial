"""Record applications the human already made, from their own pasted list.

The user applies on BOSS directly - JobAgent submits nothing - so a day of
manual applying leaves no trace here, and the funnel, the resume attribution
and the 已投递 view are all silently wrong. The authoritative record of what
was applied to is BOSS's own 沟通 list, which the human can read and copy.

Nothing in this module touches a recruitment site. The text arrives because a
human selected it and pressed Ctrl+C, exactly as Quick Capture (v0.3) already
works: no tab is opened, no conversation is clicked, no request is made. That
is deliberately *more* restrictive than the suspended M7 scan, and needs no
part of it re-enabled.

**The matching runs backwards on purpose.** It does not parse BOSS's layout -
that would be guessing at a format nobody documented, and a layout change would
turn into silently wrong records. Instead it asks which of the jobs *already
stored* appear in the pasted text. An entry with no stored job simply does not
appear, which is the required behaviour: a job is never created from a paste.

Authorized 2026-09-02: batch *recording* of 已投递 for jobs already in the
library, behind one explicit confirmation naming the exact count. Batch
*execution* of applications remains forbidden, an AI verdict may still never
write a human status, and an unmatched entry never becomes a new job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ValidationError
from app.core.logging import get_logger, log_event
from app.models import Job
from app.models.enums import JobStatus
from app.schemas.application import MarkAppliedRequest
from app.services import application_workflow

logger = get_logger(__name__)

#: A paste far larger than any conversation list is a mistake, not a list.
MAX_TEXT_CHARS = 200_000

#: Below this, a "company name" is too generic to identify anything.
MIN_COMPANY_CHARS = 2

_WHITESPACE = re.compile(r"\s+")


def _normalize(value: str | None) -> str:
    """Whitespace-free, case-folded text for containment checks.

    BOSS wraps and pads its list differently from the job library, so a match
    must not depend on spacing. Nothing else is altered: dropping punctuation
    too would start matching different companies to each other.
    """
    return _WHITESPACE.sub("", (value or "")).casefold()


@dataclass(slots=True)
class BackfillMatch:
    """One stored job found in the pasted text."""

    job_id: int
    company: str
    title: str
    status: str
    #: True when both the company and the job title were found. A company-only
    #: hit is reported separately and never pre-selected: several roles at one
    #: company are common, and picking the wrong one records an application
    #: that did not happen.
    title_matched: bool
    #: False when the workflow forbids applied from this job's current status
    #: (a skipped job must be restored first). Reported, never worked around.
    can_apply: bool
    reason: str = ""


@dataclass(slots=True)
class BackfillPlan:
    """What a confirmation would record. Pure read - writes nothing."""

    #: Company and title both present, and the transition is legal.
    confident: list[BackfillMatch] = field(default_factory=list)
    #: Company matched but the title did not, or the job cannot transition.
    needs_review: list[BackfillMatch] = field(default_factory=list)
    #: Already applied before this paste - listed so the count is explicable.
    already_applied: list[BackfillMatch] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def confident_ids(self) -> list[int]:
        return [match.job_id for match in self.confident]


def plan(db: Session, text: str) -> BackfillPlan:
    """Which stored jobs appear in this text. Spends nothing, writes nothing."""
    if not text or not text.strip():
        raise ValidationError("请先粘贴 BOSS「沟通过的职位」列表内容。")
    if len(text) > MAX_TEXT_CHARS:
        raise ValidationError(
            f"粘贴内容超过 {MAX_TEXT_CHARS} 字，请分批粘贴。",
            detail={"length": len(text), "max": MAX_TEXT_CHARS},
        )

    haystack = _normalize(text)
    result = BackfillPlan()

    for job in db.scalars(select(Job)).unique():
        company = _normalize(job.company)
        if len(company) < MIN_COMPANY_CHARS or company not in haystack:
            continue
        title_matched = bool(job.title) and _normalize(job.title) in haystack
        can_apply = application_workflow.can_transition(job.status, JobStatus.applied)
        match = BackfillMatch(
            job_id=job.id,
            company=job.company,
            title=job.title,
            status=job.status.value,
            title_matched=title_matched,
            can_apply=can_apply,
        )
        if job.status is JobStatus.applied:
            match.reason = "此前已记录为已投递，不会重复记录"
            result.already_applied.append(match)
        elif not can_apply:
            match.reason = f"当前状态「{job.status.value}」不能直接标记已投递，需先恢复待处理"
            result.needs_review.append(match)
        elif not title_matched:
            match.reason = "只匹配到公司名，职位名没有出现——同一家公司可能有多个岗位"
            result.needs_review.append(match)
        else:
            result.confident.append(match)

    result.notes = _build_notes(result)
    log_event(
        logger,
        "applied_backfill.planned",
        confident=len(result.confident),
        needs_review=len(result.needs_review),
        already_applied=len(result.already_applied),
    )
    return result


def _build_notes(result: BackfillPlan) -> list[str]:
    """Template sentences, so the explanation can be re-derived and checked."""
    notes = [
        "只在已入库的岗位里匹配。BOSS 列表里有、而岗位库没有的，"
        "不会出现在这里，也不会被新建。"
    ]
    if result.needs_review:
        notes.append(
            f"{len(result.needs_review)} 个需要你自己判断，默认不勾选。"
        )
    if result.already_applied:
        notes.append(
            f"{len(result.already_applied)} 个此前已记录为已投递，不会重复记录。"
        )
    if not result.confident:
        notes.append("没有可以直接补录的岗位。")
    return notes


def confirm(
    db: Session,
    *,
    job_ids: list[int],
    confirmed: bool,
    expected_count: int,
    note: str | None = None,
) -> dict[str, object]:
    """Record 已投递 for exactly these jobs. The only writing path here.

    `expected_count` is what the confirmation dialog showed the human. It must
    equal the number of jobs actually being recorded: a selection that changed
    between reading the number and pressing the button is refused rather than
    silently recording a different set.
    """
    if not confirmed:
        raise ValidationError(
            "需要明确确认后才能批量补录已投递。", detail={"field": "confirmed"}
        )
    unique_ids = list(dict.fromkeys(job_ids))
    if not unique_ids:
        raise ValidationError("没有选中任何岗位。")
    if expected_count != len(unique_ids):
        raise ValidationError(
            f"确认时显示的数量（{expected_count}）与实际选中的数量"
            f"（{len(unique_ids)}）不一致，已取消，未记录任何岗位。",
            detail={"expected": expected_count, "selected": len(unique_ids)},
        )

    recorded: list[int] = []
    skipped: list[dict[str, object]] = []
    for job_id in unique_ids:
        try:
            # The one and only place a status changes stays
            # `application_workflow` - each job gets its own confirmed call and
            # its own event, exactly as a single manual 标记已投递 would.
            application_workflow.mark_applied(
                db,
                job_id,
                MarkAppliedRequest(
                    confirmed=True,
                    note=note or "用户确认此前已在 BOSS 完成投递（批量补录）",
                ),
            )
            recorded.append(job_id)
        except Exception as exc:  # noqa: BLE001 - one bad row must not lose the rest
            message = getattr(exc, "message", None) or str(exc)
            skipped.append({"job_id": job_id, "error": message})
            log_event(
                logger,
                "applied_backfill.item_failed",
                job_id=job_id,
                error=type(exc).__name__,
            )

    log_event(
        logger,
        "applied_backfill.recorded",
        recorded=len(recorded),
        skipped=len(skipped),
    )
    return {"recorded": recorded, "skipped": skipped}
