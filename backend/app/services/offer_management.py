"""The only place offer / revision state changes (v0.9).

Routes never touch these models directly, exactly as they never touch
``Job.status`` (``application_workflow``) or interview state
(``interview_pipeline``).

The three rules this module holds:

**An offer belongs to one application cycle.** The link is ``applied_event_id``,
resolved once at creation and never re-derived. `applied(A) -> reset ->
applied(B) -> offer` credits **B**, and the resume credited comes from B's own
``applied`` event.

**A candidate counter is not a company offer.** ``latest_company_revision``
filters on ``source``; the number the user asked for never becomes "the current
offer". This is the distinction that makes the whole negotiation view honest.

**Accepted compensation is frozen.** Accepting stores ``accepted_revision_id``,
and every later read of "what did I accept" goes through that id. A revision
added afterwards - or a correction - must never move it.

Negotiation history is append-only: correcting a revision's figures appends a
new revision pointing at the old one rather than editing it.

Compensation values are never logged. Log lines carry ids and status only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger, log_event
from app.models import (
    ApplicationEvent,
    Currency,
    EventType,
    InterviewProcess,
    Job,
    JobStatus,
    Offer,
    OfferRevision,
    OfferStatus,
    Resume,
    RevisionSource,
    RevisionType,
)
from app.schemas.application import OfferRequest
from app.schemas.offer import (
    AcceptOfferRequest,
    CloseOfferRequest,
    CompensationFields,
    CounterRequest,
    DeclineOfferRequest,
    OfferCreateRequest,
    OfferUpdateRequest,
    RevisionCreateRequest,
)
from app.services import application_workflow
from app.services.application_cycles import build_cycles, effective_cycle
from app.services.offer_calculator import (
    CompensationBreakdown,
    CompensationInputs,
    Uplift,
    compute_breakdown,
    uplift,
)

logger = get_logger(__name__)

#: Compensation fields copied from a request onto a revision row.
_COMPENSATION_FIELDS = (
    "base_salary_annual",
    "base_salary_monthly",
    "months_per_year",
    "bonus_guaranteed",
    "bonus_target",
    "signing_bonus",
    "stock_value",
    "stock_type",
    "stock_vesting_years",
    "stock_vesting_text",
    "allowances_annual",
    "overtime_pay_text",
    "housing_value",
    "transport_value",
    "other_cash_annual",
    "salary_text_original",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------


def _offer_query():
    return select(Offer).options(
        selectinload(Offer.revisions),
        selectinload(Offer.job).selectinload(Job.events),
    )


def get_offer(db: Session, offer_id: int) -> Offer:
    offer = db.scalar(_offer_query().where(Offer.id == offer_id))
    if offer is None:
        raise NotFoundError(f"Offer {offer_id} 不存在", detail={"offer_id": offer_id})
    return offer


def list_offers(db: Session, *, job_id: int | None = None) -> list[Offer]:
    stmt = _offer_query()
    if job_id is not None:
        stmt = stmt.where(Offer.job_id == job_id)
    return list(db.scalars(stmt.order_by(Offer.created_at.desc())).unique())


def offer_for_job(db: Session, job_id: int) -> Offer | None:
    """The offer on the job's *current* application cycle, if any.

    A superseded cycle's offer is history and is deliberately not returned.
    """
    job = db.get(Job, job_id)
    if job is None:
        return None
    cycle = effective_cycle(job)
    if cycle is None or cycle.applied_event_id is None:
        return None
    return db.scalar(
        _offer_query().where(Offer.applied_event_id == cycle.applied_event_id)
    )


# --------------------------------------------------------------------------
# cycle attribution
# --------------------------------------------------------------------------


@dataclass(slots=True)
class CycleContext:
    applied_event_id: int
    applied_at: datetime
    resume_id: int | None
    resume_label: str | None
    resume_archived: bool


def cycle_context(db: Session, offer: Offer) -> CycleContext | None:
    """Resolve the offer's cycle. Read-only, and never guesses.

    The resume comes from that cycle's own ``applied`` event, so it is frozen
    at application time exactly as v0.7 requires.
    """
    job = offer.job or db.get(Job, offer.job_id)
    if job is None:
        return None
    for cycle in build_cycles(job):
        if cycle.applied_event_id == offer.applied_event_id:
            resume = db.get(Resume, cycle.resume_id) if cycle.resume_id else None
            return CycleContext(
                applied_event_id=offer.applied_event_id,
                applied_at=cycle.applied_at,
                resume_id=cycle.resume_id,
                resume_label=resume.display_name if resume else None,
                resume_archived=bool(resume and resume.archived),
            )
    return None


def _resolve_applied_event(db: Session, job: Job, applied_event_id: int | None) -> int:
    cycles = build_cycles(job)
    if not cycles:
        raise ValidationError(
            "该岗位还没有投递记录，无法记录 Offer。请先确认已投递。",
            detail={"job_id": job.id},
        )
    if applied_event_id is not None:
        if not any(c.applied_event_id == applied_event_id for c in cycles):
            raise NotFoundError(
                "找不到对应的投递记录。",
                detail={"applied_event_id": applied_event_id, "job_id": job.id},
            )
        return applied_event_id

    cycle = effective_cycle(job)
    if cycle is None or cycle.applied_event_id is None:
        raise ValidationError(
            "该岗位当前没有有效的投递记录（可能已被「恢复待处理」撤销）。",
            detail={"job_id": job.id},
        )
    return cycle.applied_event_id


# --------------------------------------------------------------------------
# the company-offer / candidate-ask distinction
# --------------------------------------------------------------------------


def _ordered(offer: Offer) -> list[OfferRevision]:
    return sorted(offer.revisions, key=lambda r: (r.revision_index, r.id))


def latest_company_revision(offer: Offer) -> OfferRevision | None:
    """What the company is currently offering.

    Explicitly **not** the latest revision: if the sequence is
    ``company 330K -> candidate counter 350K``, the company's offer is still
    330K. Reading "latest" here would report the user's own ask back to them as
    an offer, which is the single most misleading thing this feature could do.
    """
    company = [r for r in _ordered(offer) if r.is_company_offer]
    return company[-1] if company else None


def latest_candidate_counter(offer: Offer) -> OfferRevision | None:
    """The most recent thing the user asked for, if anything."""
    counters = [r for r in _ordered(offer) if not r.is_company_offer]
    return counters[-1] if counters else None


def initial_company_revision(offer: Offer) -> OfferRevision | None:
    company = [r for r in _ordered(offer) if r.is_company_offer]
    return company[0] if company else None


def accepted_revision(db: Session, offer: Offer) -> OfferRevision | None:
    """What was accepted, read from the frozen id - never from "latest"."""
    if offer.accepted_revision_id is None:
        return None
    return db.get(OfferRevision, offer.accepted_revision_id)


def declined_revision(db: Session, offer: Offer) -> OfferRevision | None:
    if offer.declined_revision_id is None:
        return None
    return db.get(OfferRevision, offer.declined_revision_id)


def breakdown_of(revision: OfferRevision | None) -> CompensationBreakdown | None:
    """Derive one revision's comparable figures. Pure arithmetic."""
    if revision is None:
        return None
    return compute_breakdown(
        CompensationInputs(
            base_salary_annual=revision.base_salary_annual,
            base_salary_monthly=revision.base_salary_monthly,
            months_per_year=revision.months_per_year,
            bonus_guaranteed=revision.bonus_guaranteed,
            bonus_target=revision.bonus_target,
            signing_bonus=revision.signing_bonus,
            allowances_annual=revision.allowances_annual,
            housing_value=revision.housing_value,
            transport_value=revision.transport_value,
            other_cash_annual=revision.other_cash_annual,
            stock_value=revision.stock_value,
            stock_vesting_years=revision.stock_vesting_years,
        )
    )


# --------------------------------------------------------------------------
# negotiation
# --------------------------------------------------------------------------


@dataclass(slots=True)
class NegotiationOutcome:
    rounds: int = 0
    candidate_counters: int = 0
    company_revisions: int = 0
    base: Uplift | None = None
    first_year_guaranteed: Uplift | None = None
    first_year_target: Uplift | None = None
    signing_bonus_gained: float | None = None
    remote_changed: bool = False
    start_date_changed: bool = False
    has_negotiation_sequence: bool = False


def negotiation_summary(offer: Offer) -> NegotiationOutcome:
    """What moved between the first and current company offer.

    Only compares *company* figures - the gap between an ask and an offer is
    not an uplift. ``has_negotiation_sequence`` is true only when a counter was
    recorded and a company revision followed it, so a change is never presented
    as caused by negotiation that was not recorded.
    """
    ordered = _ordered(offer)
    company = [r for r in ordered if r.is_company_offer]
    counters = [r for r in ordered if not r.is_company_offer]

    outcome = NegotiationOutcome(
        rounds=len(ordered),
        candidate_counters=len(counters),
        company_revisions=len(company),
    )

    if len(company) >= 2:
        first, last = company[0], company[-1]
        first_break = breakdown_of(first)
        last_break = breakdown_of(last)
        outcome.base = uplift(first_break.base_annual, last_break.base_annual)
        outcome.first_year_guaranteed = uplift(
            first_break.first_year_guaranteed_cash, last_break.first_year_guaranteed_cash
        )
        outcome.first_year_target = uplift(
            first_break.first_year_target_cash, last_break.first_year_target_cash
        )
        if last.signing_bonus is not None:
            gained = float(last.signing_bonus) - float(first.signing_bonus or 0)
            outcome.signing_bonus_gained = round(gained, 2) if gained else None
        outcome.remote_changed = (
            last.requested_remote_policy is not None
            and last.requested_remote_policy != first.requested_remote_policy
        )
        outcome.start_date_changed = (
            last.requested_start_date is not None
            and last.requested_start_date != first.requested_start_date
        )

    # A counter must precede a company revision for the sequence to mean
    # anything. Without that ordering, a change is just a change.
    if counters and len(company) >= 2:
        first_counter_index = counters[0].revision_index
        outcome.has_negotiation_sequence = any(
            r.revision_index > first_counter_index for r in company[1:]
        )

    return outcome


# --------------------------------------------------------------------------
# creation
# --------------------------------------------------------------------------


def _apply_compensation(
    row: OfferRevision, payload: CompensationFields | RevisionCreateRequest
) -> None:
    for field in _COMPENSATION_FIELDS:
        value = getattr(payload, field, None)
        if value is not None:
            setattr(row, field, value)


def _next_index(offer: Offer) -> int:
    return max((r.revision_index for r in offer.revisions), default=0) + 1


def _add_revision_row(
    db: Session,
    offer: Offer,
    payload: RevisionCreateRequest | CompensationFields,
    *,
    revision_type: RevisionType,
    source: RevisionSource,
) -> OfferRevision:
    row = OfferRevision(
        offer_id=offer.id,
        revision_index=_next_index(offer),
        revision_type=revision_type,
        source=source,
        # Denormalized so a revision stays self-describing even if the offer's
        # currency is later corrected - stored history is never reinterpreted.
        currency=offer.currency,
        created_at=_now(),
    )
    _apply_compensation(row, payload)

    for field in ("requested_start_date", "requested_remote_policy", "other_request"):
        value = getattr(payload, field, None)
        if value is not None:
            setattr(row, field, value)
    row.effective_at = _as_utc(getattr(payload, "effective_at", None))
    notes = getattr(payload, "notes", None)
    row.notes = (notes or "").strip() or None
    row.corrects_revision_id = getattr(payload, "corrects_revision_id", None)

    db.add(row)
    db.flush()
    offer.revisions.append(row)
    return row


def create_offer(db: Session, job_id: int, payload: OfferCreateRequest) -> Offer:
    """Record an offer on one application cycle.

    Requires ``confirmed``: an offer is a real thing that happened outside
    JobAgent. Refuses a duplicate - one cycle, one offer.
    """
    if not payload.confirmed:
        raise ValidationError(
            "需要明确确认后才能记录 Offer。", detail={"field": "confirmed"}
        )

    job = application_workflow.get_job(db, job_id)
    applied_event_id = _resolve_applied_event(db, job, payload.applied_event_id)

    existing = db.scalar(select(Offer).where(Offer.applied_event_id == applied_event_id))
    if existing is not None:
        raise ValidationError(
            "这次投递已经记录过 Offer 了，可以在其中添加谈薪记录。",
            detail={"offer_id": existing.id, "applied_event_id": applied_event_id},
        )

    process_id = payload.interview_process_id
    if process_id is not None and db.get(InterviewProcess, process_id) is None:
        raise NotFoundError(
            f"面试流程 {process_id} 不存在", detail={"interview_process_id": process_id}
        )
    if process_id is None:
        # Link the cycle's own interview process when there is one. Offers that
        # skipped interviews entirely are perfectly valid and stay unlinked.
        linked = db.scalar(
            select(InterviewProcess).where(
                InterviewProcess.applied_event_id == applied_event_id
            )
        )
        process_id = linked.id if linked else None

    offer = Offer(
        job_id=job.id,
        applied_event_id=applied_event_id,
        interview_process_id=process_id,
        status=OfferStatus.received,
        currency=payload.currency,
        received_at=_as_utc(payload.received_at) or _now(),
        decision_deadline=_as_utc(payload.decision_deadline),
        proposed_start_date=payload.proposed_start_date,
        employment_type=payload.employment_type,
        work_location=(payload.work_location or "").strip() or None,
        remote_policy=payload.remote_policy,
        probation_text=(payload.probation_text or "").strip() or None,
        benefits_json=dict(payload.benefits or {}),
        notes=(payload.notes or "").strip() or None,
    )
    db.add(offer)
    db.flush()

    if payload.initial is not None:
        _add_revision_row(
            db,
            offer,
            payload.initial,
            revision_type=RevisionType.initial,
            source=RevisionSource.company,
        )

    application_workflow._append_event(  # noqa: SLF001 - same package boundary
        db,
        job,
        EventType.offer_received,
        notes="记录 Offer",
        metadata={"offer_id": offer.id, "applied_event_id": applied_event_id},
    )
    db.commit()
    # Never log compensation - ids and status only.
    log_event(
        logger,
        "offer.created",
        offer_id=offer.id,
        job_id=job.id,
        applied_event_id=applied_event_id,
        currency=offer.currency.value,
    )
    return get_offer(db, offer.id)


def record_offer_from_workflow(
    db: Session, job_id: int, payload: OfferRequest
) -> Offer | None:
    """The v0.4 记录Offer action, delegated into offer management.

    Creates the offer row on the current cycle so there is no parallel path.
    Returns ``None`` when the cycle already has one - re-recording an offer
    milestone must not fail, and must not create a second offer.
    """
    existing = offer_for_job(db, job_id)
    if existing is not None:
        return existing

    job = db.get(Job, job_id)
    if job is None or effective_cycle(job) is None:
        return None

    initial = (
        CompensationFields(salary_text_original=payload.salary_text.strip())
        if payload.salary_text
        else None
    )
    return create_offer(
        db,
        job_id,
        OfferCreateRequest(confirmed=True, notes=payload.note, initial=initial),
    )


# --------------------------------------------------------------------------
# revisions
# --------------------------------------------------------------------------


def add_revision(
    db: Session, offer_id: int, payload: RevisionCreateRequest
) -> tuple[Offer, OfferRevision]:
    offer = get_offer(db, offer_id)
    if offer.is_closed:
        raise ValidationError(
            "该 Offer 已经结束，不能再添加谈薪记录。",
            detail={"status": offer.status.value},
        )

    if payload.corrects_revision_id is not None:
        target = db.get(OfferRevision, payload.corrects_revision_id)
        if target is None or target.offer_id != offer.id:
            raise NotFoundError(
                "找不到要更正的记录。",
                detail={"revision_id": payload.corrects_revision_id},
            )

    row = _add_revision_row(
        db,
        offer,
        payload,
        revision_type=payload.revision_type,
        source=payload.source,
    )

    is_correction = payload.corrects_revision_id is not None
    if is_correction:
        event_type = EventType.offer_revision_corrected
        note = "更正 Offer 记录"
    elif payload.source is RevisionSource.candidate:
        event_type = EventType.offer_countered
        note = "记录我方谈薪诉求"
    else:
        event_type = EventType.offer_revised
        note = "记录公司调整后的 Offer"

    # Negotiating starts when the company revises or the candidate counters -
    # but a closed offer never reopens, and a correction is not negotiation.
    if not is_correction and offer.status is OfferStatus.received:
        offer.status = OfferStatus.negotiating

    application_workflow._append_event(  # noqa: SLF001
        db,
        offer.job,
        event_type,
        notes=note,
        metadata=_revision_event_metadata(offer, row),
    )
    db.commit()
    log_event(
        logger,
        "offer.revision_added",
        offer_id=offer.id,
        revision_id=row.id,
        source=row.source.value,
        revision_type=row.revision_type.value,
    )
    return get_offer(db, offer_id), db.get(OfferRevision, row.id)


def record_counter(
    db: Session, offer_id: int, payload: CounterRequest
) -> tuple[Offer, OfferRevision]:
    """我的谈薪诉求 - what you intend to ask for.

    Stored, never sent. JobAgent messages no one.
    """
    return add_revision(
        db,
        offer_id,
        RevisionCreateRequest(
            revision_type=RevisionType.candidate_counter,
            source=RevisionSource.candidate,
            **payload.model_dump(),
        ),
    )


def _revision_event_metadata(offer: Offer, row: OfferRevision) -> dict[str, Any]:
    """Ids and shape only.

    Compensation deliberately excluded: the event trail is a milestone log, not
    a second copy of the offer, and salary figures must not spread into it.
    """
    return {
        "offer_id": offer.id,
        "offer_revision_id": row.id,
        "revision_index": row.revision_index,
        "revision_type": row.revision_type.value,
        "source": row.source.value,
        "applied_event_id": offer.applied_event_id,
    }


# --------------------------------------------------------------------------
# offer-level edits
# --------------------------------------------------------------------------


def update_offer(db: Session, offer_id: int, payload: OfferUpdateRequest) -> Offer:
    """Correct offer metadata. Negotiation history is untouched."""
    offer = get_offer(db, offer_id)

    if payload.clear_decision_deadline:
        offer.decision_deadline = None
    elif payload.decision_deadline is not None:
        offer.decision_deadline = _as_utc(payload.decision_deadline)

    if payload.proposed_start_date is not None:
        offer.proposed_start_date = payload.proposed_start_date
    if payload.employment_type is not None:
        offer.employment_type = payload.employment_type
    if payload.work_location is not None:
        offer.work_location = (payload.work_location or "").strip() or None
    if payload.remote_policy is not None:
        offer.remote_policy = payload.remote_policy
    if payload.probation_text is not None:
        offer.probation_text = (payload.probation_text or "").strip() or None
    if payload.benefits is not None:
        offer.benefits_json = dict(payload.benefits)
    if payload.notes is not None:
        offer.notes = (payload.notes or "").strip() or None

    db.commit()
    log_event(logger, "offer.updated", offer_id=offer.id)
    return get_offer(db, offer_id)


# --------------------------------------------------------------------------
# decisions
# --------------------------------------------------------------------------


def _resolve_decision_revision(
    db: Session, offer: Offer, revision_id: int | None
) -> OfferRevision | None:
    """Which revision a decision is being made against.

    Defaults to the current *company* offer, never the latest revision - you
    accept what the company offered, not what you asked for.
    """
    if revision_id is not None:
        row = db.get(OfferRevision, revision_id)
        if row is None or row.offer_id != offer.id:
            raise NotFoundError(
                "找不到对应的 Offer 记录。", detail={"revision_id": revision_id}
            )
        if not row.is_company_offer:
            raise ValidationError(
                "不能针对「我方诉求」做决定 —— 那是你的要求，不是公司的 Offer。",
                detail={"revision_id": revision_id, "source": row.source.value},
            )
        return row
    return latest_company_revision(offer)


def accept_offer(db: Session, offer_id: int, payload: AcceptOfferRequest) -> Offer:
    """Accept an offer, freezing exactly which revision was accepted.

    ``accepted_revision_id`` is what every later read uses. A revision added
    afterwards, or a correction, must not move the compensation this decision
    was made on.
    """
    if not payload.confirmed:
        raise ValidationError(
            "需要明确确认后才能接受 Offer。", detail={"field": "confirmed"}
        )
    offer = get_offer(db, offer_id)
    if offer.is_closed:
        raise ValidationError(
            "该 Offer 已经结束了。", detail={"status": offer.status.value}
        )

    revision = _resolve_decision_revision(db, offer, payload.revision_id)
    offer.status = OfferStatus.accepted
    offer.accepted_revision_id = revision.id if revision else None
    offer.decided_at = _as_utc(payload.decided_at) or _now()
    if payload.notes:
        offer.notes = payload.notes.strip() or offer.notes

    application_workflow._append_event(  # noqa: SLF001
        db,
        offer.job,
        EventType.offer_accepted,
        notes=payload.notes or "接受 Offer",
        metadata={
            "offer_id": offer.id,
            "accepted_revision_id": offer.accepted_revision_id,
            "applied_event_id": offer.applied_event_id,
        },
    )
    db.commit()
    log_event(
        logger,
        "offer.accepted",
        offer_id=offer.id,
        accepted_revision_id=offer.accepted_revision_id,
    )
    return get_offer(db, offer_id)


def decline_offer(db: Session, offer_id: int, payload: DeclineOfferRequest) -> Offer:
    """Decline an offer.

    The candidate's decision, never an employer rejection - ``Job.status`` is
    deliberately not moved to ``rejected`` here, and analytics counts the two
    separately.
    """
    if not payload.confirmed:
        raise ValidationError(
            "需要明确确认后才能拒绝 Offer。", detail={"field": "confirmed"}
        )
    offer = get_offer(db, offer_id)
    if offer.is_closed:
        raise ValidationError(
            "该 Offer 已经结束了。", detail={"status": offer.status.value}
        )

    revision = _resolve_decision_revision(db, offer, payload.revision_id)
    offer.status = OfferStatus.declined
    offer.declined_revision_id = revision.id if revision else None
    offer.decline_reason = payload.reason
    offer.decided_at = _as_utc(payload.decided_at) or _now()
    if payload.notes:
        offer.notes = payload.notes.strip() or offer.notes

    application_workflow._append_event(  # noqa: SLF001
        db,
        offer.job,
        EventType.offer_declined,
        notes=payload.notes or "拒绝 Offer",
        metadata={
            "offer_id": offer.id,
            "declined_revision_id": offer.declined_revision_id,
            "decline_reason": payload.reason.value,
            "applied_event_id": offer.applied_event_id,
        },
    )
    db.commit()
    log_event(
        logger, "offer.declined", offer_id=offer.id, reason=payload.reason.value
    )
    return get_offer(db, offer_id)


def close_offer(
    db: Session, offer_id: int, payload: CloseOfferRequest, *, status: OfferStatus
) -> Offer:
    """Mark an offer withdrawn by the company, or expired.

    Expiry is an explicit human confirmation, never applied automatically just
    because a deadline passed - the board *displays* a passed deadline and
    leaves the judgement to the user.
    """
    if not payload.confirmed:
        raise ValidationError("需要明确确认。", detail={"field": "confirmed"})
    offer = get_offer(db, offer_id)
    if offer.is_closed:
        raise ValidationError(
            "该 Offer 已经结束了。", detail={"status": offer.status.value}
        )

    offer.status = status
    offer.decided_at = _now()
    if payload.notes:
        offer.notes = payload.notes.strip() or offer.notes
    db.commit()
    log_event(logger, "offer.closed", offer_id=offer.id, status=status.value)
    return get_offer(db, offer_id)


# --------------------------------------------------------------------------
# legacy
# --------------------------------------------------------------------------


def legacy_offer_events(db: Session) -> list[ApplicationEvent]:
    """Pre-v0.9 ``offer`` events with no Offer row on their cycle.

    Surfaced as milestones with unknown compensation detail. A v0.4 event
    carries at most a salary string; turning that into a structured offer with
    a base, a bonus and a package would be inventing figures.
    """
    offers = {o.applied_event_id for o in db.scalars(select(Offer))}
    rows: list[ApplicationEvent] = []

    for job in db.scalars(
        select(Job).options(selectinload(Job.events), selectinload(Job.offers))
    ).unique():
        if job.offers:
            continue
        cycle_ids = {c.applied_event_id for c in build_cycles(job)}
        if cycle_ids & offers:
            continue
        for event in job.events:
            if event.event_type is EventType.offer and not (
                event.metadata_json or {}
            ).get("offer_id"):
                rows.append(event)

    rows.sort(key=lambda e: (_as_utc(e.created_at), e.id), reverse=True)
    return rows
