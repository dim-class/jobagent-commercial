"""Deleting job rows the human never acted on.

A library full of postings the model scored in the thirties is noise when
browsing, and every one of them is also a row the queue loads on each read.
This removes them - but only the ones nobody ever decided anything about.

The rule that makes deletion safe here is that **a decision is never thrown
away**. `ApplicationEvent` is an append-only trail and the analytics are built
on it, so deleting a job the human applied to, skipped, replied to, or ran an
interview or offer against would quietly rewrite history: the funnel would show
fewer applications than really happened, and a per-variant conversion rate
would move. Those jobs are reported as protected and left exactly where they
are, whatever they scored.

Nothing here is automatic. There is a plan (pure read) and a run that needs an
explicit confirmation carrying the exact count, the same shape every
irreversible action in this codebase uses.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import ValidationError
from app.core.logging import get_logger, log_event
from app.models import ApplicationEvent, EventType, Job, JobStatus

logger = get_logger(__name__)

#: Events that record a decision a person made. `viewed`, `analyzed`, `saved`,
#: `note` and `greeting_copied` are not decisions - they are the machine's own
#: bookkeeping, or a glance - so they never protect a row.
HUMAN_DECISIONS: frozenset[EventType] = frozenset(
    {
        EventType.skipped,
        EventType.applied,
        EventType.replied,
        EventType.interview,
        EventType.offer,
        EventType.rejected,
        EventType.later,
        EventType.status_reset,
        EventType.candidate_reply,
        EventType.application_result_unknown,
        EventType.application_resume_attributed,
        EventType.application_resume_changed,
    }
)

#: A status the human moved the job to. `new` is the only one nobody chose.
DECIDED_STATUSES: frozenset[JobStatus] = frozenset(
    set(JobStatus) - {JobStatus.new, JobStatus.reviewed}
)


@dataclass
class CleanupPlan:
    """What a cleanup would do. Reading this deletes nothing."""

    threshold: int
    analyzed: int = 0
    #: Below the threshold and never decided on - these would be deleted.
    deletable: list[int] = field(default_factory=list)
    #: Below the threshold but carrying a human decision - kept, and named so
    #: the number is explicable rather than just smaller than expected.
    protected: list[int] = field(default_factory=list)
    #: Never analyzed, so there is no score to judge them by - never touched.
    unscored: int = 0

    @property
    def deletable_count(self) -> int:
        return len(self.deletable)

    @property
    def protected_count(self) -> int:
        return len(self.protected)


def _latest_score(job: Job) -> int | None:
    analyses = sorted(job.analyses, key=lambda a: a.id)
    return analyses[-1].overall_score if analyses else None


def plan(db: Session, *, threshold: int) -> CleanupPlan:
    """Which jobs a cleanup at this threshold would remove. Pure read."""
    if not 0 <= threshold <= 100:
        raise ValidationError("分数阈值必须在 0-100 之间。")

    jobs = list(
        db.scalars(
            select(Job).options(selectinload(Job.analyses), selectinload(Job.events))
        ).unique()
    )
    result = CleanupPlan(threshold=threshold)
    for job in jobs:
        score = _latest_score(job)
        if score is None:
            result.unscored += 1
            continue
        result.analyzed += 1
        if score >= threshold:
            continue
        decided = job.status in DECIDED_STATUSES or any(
            event.event_type in HUMAN_DECISIONS for event in job.events
        )
        (result.protected if decided else result.deletable).append(job.id)
    return result


def run(db: Session, *, threshold: int, expected_count: int, confirmed: bool) -> dict:
    """Delete the planned jobs. Irreversible, so it is confirmed twice over.

    ``expected_count`` must equal what the plan reports *now*: a selection that
    moved between reading the dialog and pressing the button cancels rather
    than deleting a different set - exactly the rule `applied_backfill` follows
    for recording applications.
    """
    if not confirmed:
        raise ValidationError("未确认，未删除任何岗位。")
    current = plan(db, threshold=threshold)
    if current.deletable_count != expected_count:
        raise ValidationError(
            "岗位数量已变化（现在是 "
            f"{current.deletable_count} 个，确认时是 {expected_count} 个），未删除任何岗位。"
            "请重新生成清理计划。"
        )
    if not current.deletable:
        return {"deleted": 0, "protected": current.protected_count}

    for job in db.scalars(select(Job).where(Job.id.in_(current.deletable))).unique():
        db.delete(job)
    db.commit()
    log_event(
        logger,
        "jobs.cleaned",
        threshold=threshold,
        deleted=current.deletable_count,
        protected=current.protected_count,
    )
    return {"deleted": current.deletable_count, "protected": current.protected_count}
