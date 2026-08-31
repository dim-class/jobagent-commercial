"""M6: the per-job human confirmation gate for executing one application.

THE APPROVAL RECORD IS THE ONLY AUTHORITY. Not a feature flag, not an AI
verdict, not a queue position, not a previous approval. See CLAUDE.md,
"M6 - human-confirmed single application execution".

Five operations, and the separation is the point:

``request_approval``  the human confirms one named job, having been shown the
                      exact company/title/URL/resume/answers. Writes the
                      snapshot. Executes nothing.

``validate``          pure read. Answers "is this approval still good, and does
                      the page in front of us match it?" - and says *why* not.
                      Never mutates, so a caller cannot accidentally consume an
                      approval by checking it.

``begin_attempt``     atomically changes pending to executing immediately
                      before browser mutation. A claimed approval can never be
                      claimed again, including after an unknown result.

``record_outcome``    consumes the approval exactly once and records what
                      happened. A verified acceptance goes through the existing
                      ``application_workflow.mark_applied``; an unverifiable
                      result is recorded as ``application_result_unknown`` and
                      leaves ``Job.status`` alone.

There is deliberately no bulk anything here: no list of job ids, no "approve
all", no filter-based approval. One call authorizes one job.

This module never drives a browser and never talks to a recruitment site.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger, log_event
from app.models import (
    ApplicationApproval,
    ApplicationEvent,
    EventType,
    Job,
    JobStatus,
    Resume,
)
from app.schemas.application import MarkAppliedRequest
from app.services import application_workflow
from app.services.hashing import hash_text
from app.services.urls import canonical_url

logger = get_logger(__name__)

#: Fixed reasons. Never a free-text excuse and never a raw exception body.
INVALID_NO_APPROVAL = "no_approval"
INVALID_STATE = "not_pending"
INVALID_JOB_CHANGED = "job_changed"
INVALID_RESUME_CHANGED = "resume_changed"
INVALID_ANSWERS_CHANGED = "answers_changed"
INVALID_ANSWERS_SOURCE = "answers_source_invalid"
INVALID_IDENTITY_MISMATCH = "page_identity_mismatch"
INVALID_ALREADY_APPLIED = "already_applied"

#: `unknown` is an answer, not a gap - see the model docstring.
OUTCOME_APPLIED = "applied"
OUTCOME_UNKNOWN = "unknown"
OUTCOME_FAILED = "failed"
ANSWERS_SOURCE_BOSS_DYNAMIC = "boss_dynamic_unverified"


@dataclass(slots=True)
class ApprovalCheck:
    """Why an approval may or may not be executed. Read-only."""

    ok: bool
    reason: str | None = None


def _answers_hash(answers: str) -> str:
    return hash_text(answers or "")


def _resume_fingerprint(resume: Resume) -> str:
    return resume.content_hash or hash_text(resume.raw_text or "")


def get_approval(db: Session, approval_id: int) -> ApplicationApproval:
    approval = db.get(ApplicationApproval, approval_id)
    if approval is None:
        raise NotFoundError("投递确认不存在。", detail={"approval_id": approval_id})
    return approval


def latest_pending(db: Session, job_id: int) -> ApplicationApproval | None:
    return db.scalar(
        select(ApplicationApproval)
        .where(
            ApplicationApproval.job_id == job_id,
            ApplicationApproval.state == "pending",
        )
        .order_by(ApplicationApproval.id.desc())
    )


def request_approval(
    db: Session,
    job_id: int,
    *,
    resume_id: int,
    answers: str,
    answers_source: str,
    confirmed: bool,
) -> ApplicationApproval:
    """Record one human confirmation for one job.

    Refuses without ``confirmed=True``. Snapshots exactly what the human was
    shown, so any later drift is detectable rather than assumed away.
    """
    if not confirmed:
        raise ValidationError(
            "需要逐个岗位明确确认后才能授权投递。", detail={"field": "confirmed"}
        )
    if answers_source != ANSWERS_SOURCE_BOSS_DYNAMIC:
        raise ValidationError(
            "只允许逐岗位确认 BOSS 的未知动态首次招呼语。",
            detail={"field": "answers_source", "reason": INVALID_ANSWERS_SOURCE},
        )
    if answers:
        raise ValidationError(
            "BOSS 首次招呼语无法预览或控制，确认中不得填写或猜测正文。",
            detail={"field": "answers_text"},
        )

    job = db.get(Job, job_id)
    if job is None:
        raise NotFoundError(f"岗位 {job_id} 不存在", detail={"job_id": job_id})
    if job.status == JobStatus.applied:
        raise ValidationError(
            "该岗位已记录为已投递，不能再次授权投递。",
            detail={"job_id": job_id, "reason": INVALID_ALREADY_APPLIED},
        )

    url = canonical_url(job.source_url)
    if not url or not job.external_id:
        raise ValidationError(
            "该岗位没有可校验的原始链接或岗位编号，无法授权投递。",
            detail={"job_id": job_id},
        )

    resume = db.get(Resume, resume_id)
    if resume is None:
        raise NotFoundError(f"简历 {resume_id} 不存在", detail={"resume_id": resume_id})
    if resume.archived_at is not None:
        raise ValidationError("已归档的简历不能用于投递。", detail={"resume_id": resume_id})

    # An already-claimed attempt may be in the browser right now. A second
    # approval at that point could authorize a duplicate application, so fail
    # closed until the first attempt has a terminal outcome.
    executing = db.scalar(
        select(ApplicationApproval).where(
            ApplicationApproval.job_id == job_id,
            ApplicationApproval.state == "executing",
        )
    )
    if executing is not None:
        raise ValidationError(
            "该岗位已有一次投递尝试正在执行或等待核对，不能生成第二份确认。",
            detail={"job_id": job_id, "reason": INVALID_STATE},
        )

    # A fresh confirmation supersedes any earlier unused one for this job, so
    # two pending approvals for the same job can never both be executable.
    for stale in db.scalars(
        select(ApplicationApproval).where(
            ApplicationApproval.job_id == job_id,
            ApplicationApproval.state == "pending",
        )
    ):
        stale.state = "invalidated"
        stale.invalidated_reason = "superseded"

    approval = ApplicationApproval(
        job_id=job.id,
        company=job.company,
        title=job.title,
        canonical_url=url,
        external_id=job.external_id,
        resume_id=resume.id,
        resume_hash=_resume_fingerprint(resume),
        answers_text="",
        answers_hash=_answers_hash(answers),
        answers_source=answers_source,
        state="pending",
    )
    db.add(approval)
    db.commit()
    db.refresh(approval)

    # Ids and the fixed empty compatibility marker only. No greeting body is
    # accepted, persisted or logged in this mode.
    log_event(
        logger,
        "application.approval_requested",
        approval_id=approval.id,
        job_id=job.id,
        resume_id=resume.id,
        answers_chars=len(approval.answers_text),
    )
    return approval


def validate(
    db: Session,
    approval: ApplicationApproval,
    *,
    observed_url: str | None = None,
    observed_external_id: str | None = None,
) -> ApprovalCheck:
    """Is this approval still executable, and does the page match it?

    Pure read - checking never consumes. Re-run immediately before submitting,
    not only at confirmation time.
    """
    if approval.state != "pending":
        return ApprovalCheck(False, INVALID_STATE)

    job = db.get(Job, approval.job_id)
    if job is None:
        return ApprovalCheck(False, INVALID_JOB_CHANGED)
    if job.status == JobStatus.applied:
        return ApprovalCheck(False, INVALID_ALREADY_APPLIED)
    if (
        job.company != approval.company
        or job.title != approval.title
        or canonical_url(job.source_url) != approval.canonical_url
        or (job.external_id or "") != approval.external_id
    ):
        return ApprovalCheck(False, INVALID_JOB_CHANGED)

    resume = db.get(Resume, approval.resume_id)
    if resume is None or _resume_fingerprint(resume) != approval.resume_hash:
        return ApprovalCheck(False, INVALID_RESUME_CHANGED)

    if _answers_hash(approval.answers_text) != approval.answers_hash:
        return ApprovalCheck(False, INVALID_ANSWERS_CHANGED)
    if approval.answers_source != ANSWERS_SOURCE_BOSS_DYNAMIC:
        return ApprovalCheck(False, INVALID_ANSWERS_SOURCE)

    # The page in front of the user must be the job that was confirmed. Both
    # halves are checked: a matching id on a different URL is still a mismatch.
    if observed_url is not None or observed_external_id is not None:
        if canonical_url(observed_url) != approval.canonical_url:
            return ApprovalCheck(False, INVALID_IDENTITY_MISMATCH)
        if (observed_external_id or "") != approval.external_id:
            return ApprovalCheck(False, INVALID_IDENTITY_MISMATCH)

    return ApprovalCheck(True)


def begin_attempt(
    db: Session,
    approval_id: int,
    *,
    observed_url: str,
    observed_external_id: str,
) -> ApplicationApproval:
    """Atomically claim the only browser attempt this approval can authorize."""
    approval = get_approval(db, approval_id)
    check = validate(
        db,
        approval,
        observed_url=observed_url,
        observed_external_id=observed_external_id,
    )
    if not check.ok:
        raise ValidationError(
            "投递确认已失效，浏览器不会执行。",
            detail={"approval_id": approval.id, "reason": check.reason},
        )

    started = datetime.now(timezone.utc)
    claimed = db.execute(
        update(ApplicationApproval)
        .where(ApplicationApproval.id == approval.id, ApplicationApproval.state == "pending")
        .values(state="executing", attempt_started_at=started)
    )
    if claimed.rowcount != 1:
        db.rollback()
        raise ValidationError(
            "该投递确认已经被使用或正在执行，不会重复尝试。",
            detail={"approval_id": approval.id, "reason": INVALID_STATE},
        )
    db.commit()
    db.refresh(approval)
    log_event(
        logger,
        "application.attempt_claimed",
        approval_id=approval.id,
        job_id=approval.job_id,
        answers_source=approval.answers_source,
    )
    return approval


def invalidate(db: Session, approval: ApplicationApproval, *, reason: str) -> ApplicationApproval:
    """Retire a pending approval without executing anything."""
    if approval.state == "pending":
        approval.state = "invalidated"
        approval.invalidated_reason = reason
        db.commit()
        db.refresh(approval)
    return approval


def abandon_attempt(
    db: Session, approval_id: int, *, confirmed: bool
) -> ApplicationApproval:
    """Let the human close out an attempt whose result never came back.

    If the worker dies between ``begin_attempt`` and ``record_outcome`` the
    approval stays ``executing`` forever: never auto-retried (which is correct)
    but also never resolvable, so the job is permanently blocked from M6 with
    nothing on its trail explaining why.

    The person can look at BOSS themselves; the system cannot. So this records
    the only honest answer - ``unknown`` - and **can never record success**:
    there is no outcome parameter to pass. ``Job.status`` is untouched, no
    retry happens, and a fresh confirmation becomes possible again.
    """
    if not confirmed:
        raise ValidationError(
            "需要明确确认后才能结束这次未返回结果的投递尝试。",
            detail={"field": "confirmed"},
        )
    approval = get_approval(db, approval_id)
    if approval.state != "executing":
        raise ValidationError(
            "只有已领取但没有结果的投递尝试可以由人工结束。",
            detail={"approval_id": approval.id, "reason": INVALID_STATE},
        )

    approval.state = "consumed"
    approval.outcome = OUTCOME_UNKNOWN
    approval.outcome_detail = "人工结束：浏览器未返回结果，需自行到招聘平台核对"
    approval.consumed_at = datetime.now(timezone.utc)
    db.add(
        ApplicationEvent(
            job_id=approval.job_id,
            event_type=EventType.application_result_unknown,
            notes="投递尝试未返回结果，已由本人结束；请到招聘平台核对后手动标记",
            metadata_json={
                "approval_id": approval.id,
                "outcome": OUTCOME_UNKNOWN,
                "resolved_by": "human",
            },
        )
    )
    db.commit()
    db.refresh(approval)
    log_event(
        logger,
        "application.attempt_abandoned",
        approval_id=approval.id,
        job_id=approval.job_id,
    )
    return approval


def record_outcome(
    db: Session,
    approval_id: int,
    *,
    outcome: str,
    detail: str | None = None,
    observed_url: str | None = None,
    observed_external_id: str | None = None,
) -> ApplicationApproval:
    """Consume the approval once and record what actually happened.

    ``applied`` is written **only** for a verified acceptance, and only through
    the existing ``application_workflow.mark_applied`` - there is no second
    path to that status. Anything unverifiable is ``unknown``: recorded, never
    upgraded, never silently dropped.
    """
    if outcome not in {OUTCOME_APPLIED, OUTCOME_UNKNOWN, OUTCOME_FAILED}:
        raise ValidationError("未知的投递结果。", detail={"outcome": outcome})

    # Recording an outside-world result is stricter than a read-only status
    # check: both pieces of observed page identity are mandatory.  Omitting
    # them must never turn a backend caller into an implicit browser witness.
    if not observed_url or not observed_external_id:
        raise ValidationError(
            "缺少投递时的页面身份，未记录任何结果。",
            detail={"reason": INVALID_IDENTITY_MISMATCH},
        )

    approval = get_approval(db, approval_id)
    if approval.state != "executing":
        raise ValidationError(
            "该投递确认没有已领取的执行尝试，未记录结果。",
            detail={"approval_id": approval.id, "reason": INVALID_STATE},
        )
    # Re-check every snapshot field after the claim. The state is deliberately
    # excluded here: executing is required above, while validate() is the
    # pending-only preflight.
    job = db.get(Job, approval.job_id)
    resume = db.get(Resume, approval.resume_id)
    snapshot_ok = bool(
        job
        and job.status != JobStatus.applied
        and job.company == approval.company
        and job.title == approval.title
        and canonical_url(job.source_url) == approval.canonical_url
        and (job.external_id or "") == approval.external_id
        and resume
        and _resume_fingerprint(resume) == approval.resume_hash
        and _answers_hash(approval.answers_text) == approval.answers_hash
        and approval.answers_source == ANSWERS_SOURCE_BOSS_DYNAMIC
        and canonical_url(observed_url) == approval.canonical_url
        and observed_external_id == approval.external_id
    )
    if not snapshot_ok:
        raise ValidationError(
            "投递执行期间身份或确认内容发生变化，未记录成功。",
            detail={"approval_id": approval.id, "reason": INVALID_IDENTITY_MISMATCH},
        )

    approval.state = "consumed"
    approval.outcome = outcome
    approval.outcome_detail = (detail or None) and str(detail)[:256]
    approval.consumed_at = datetime.now(timezone.utc)

    if outcome == OUTCOME_APPLIED:
        # The single existing path. It asserts the transition, writes exactly
        # one `applied` event and carries the resume attribution (v0.7).
        result = application_workflow.mark_applied(
            db,
            approval.job_id,
            MarkAppliedRequest(
                confirmed=True,
                resume_id=approval.resume_id,
                note="扩展执行了本人逐岗位确认的一次投递，并已确认站点接受",
            ),
        )
        approval.applied_event_id = result.event.id
    else:
        # Not a success and not a failure of the human's decision: the site's
        # answer could not be read. `Job.status` is deliberately untouched.
        db.add(
            ApplicationEvent(
                job_id=approval.job_id,
                event_type=EventType.application_result_unknown
                if outcome == OUTCOME_UNKNOWN
                else EventType.note,
                notes=(
                    "投递结果无法确认，请到招聘平台核对后手动标记"
                    if outcome == OUTCOME_UNKNOWN
                    else "投递未完成，未记录为已投递"
                ),
                metadata_json={
                    "approval_id": approval.id,
                    "outcome": outcome,
                    "answers_source": approval.answers_source,
                },
            )
        )

    db.commit()
    db.refresh(approval)
    log_event(
        logger,
        "application.approval_consumed",
        approval_id=approval.id,
        job_id=approval.job_id,
        outcome=outcome,
        applied_event_id=approval.applied_event_id,
    )
    return approval
