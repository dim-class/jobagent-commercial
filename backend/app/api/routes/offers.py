"""Offer endpoints (v0.9).

Thin routes: every state change goes through ``services/offer_management``,
which is to offers what ``application_workflow`` is to ``Job.status``.

JobAgent negotiates nothing and accepts nothing. Every action here records a
decision the human already made, or intends to make, outside the app.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ValidationError
from app.db.session import get_db
from app.models import (
    DECLINE_REASON_LABEL,
    OFFER_STATUS_LABEL,
    REVISION_TYPE_LABEL,
    Currency,
    Offer,
    OfferRevision,
    OfferStatus,
)
from app.schemas.offer import (
    AcceptOfferRequest,
    CloseOfferRequest,
    CompensationSummary,
    CounterRequest,
    DeclineOfferRequest,
    LegacyOfferMilestone,
    NegotiationSummary,
    OfferActionResponse,
    OfferBoardResponse,
    OfferComparisonResponse,
    OfferComparisonRow,
    OfferCreateRequest,
    OfferListResponse,
    OfferOut,
    OfferUpdateRequest,
    ParsedSalaryOut,
    ParseOfferTextRequest,
    RevisionCreateRequest,
    RevisionOut,
    UpliftOut,
)
from app.services import offer_management
from app.services.offer_calculator import CompensationBreakdown, parse_offer_salary
from app.services.timezones import local_now, to_local

router = APIRouter(prefix="/api", tags=["offers"])

#: How many offers may be compared at once. More than four stops being a
#: comparison and starts being a spreadsheet.
MAX_COMPARE = 4
#: Deadlines within this many days are called out on the board.
SOON_DAYS = 3


def _utc(moment: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; comparing one to an aware ``now`` raises."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


# --------------------------------------------------------------------------
# serialization
# --------------------------------------------------------------------------


def _summary(breakdown: CompensationBreakdown | None, currency: Currency):
    if breakdown is None:
        return None
    return CompensationSummary(
        currency=currency,
        base_annual=breakdown.base_annual,
        first_year_guaranteed_cash=breakdown.first_year_guaranteed_cash,
        first_year_target_cash=breakdown.first_year_target_cash,
        steady_state_guaranteed_cash=breakdown.steady_state_guaranteed_cash,
        steady_state_target_cash=breakdown.steady_state_target_cash,
        equity_annualized=breakdown.equity_annualized,
        estimated_first_year_total_comp=breakdown.estimated_first_year_total_comp,
        equity_excluded=breakdown.equity_excluded,
        notes=breakdown.notes,
    )


def _revision_out(row: OfferRevision) -> RevisionOut:
    return RevisionOut(
        id=row.id,
        offer_id=row.offer_id,
        revision_index=row.revision_index,
        revision_type=row.revision_type,
        revision_type_label=REVISION_TYPE_LABEL[row.revision_type],
        source=row.source,
        is_company_offer=row.is_company_offer,
        base_salary_annual=row.base_salary_annual,
        base_salary_monthly=row.base_salary_monthly,
        months_per_year=row.months_per_year,
        bonus_guaranteed=row.bonus_guaranteed,
        bonus_target=row.bonus_target,
        signing_bonus=row.signing_bonus,
        stock_value=row.stock_value,
        stock_type=row.stock_type,
        stock_vesting_years=row.stock_vesting_years,
        stock_vesting_text=row.stock_vesting_text,
        allowances_annual=row.allowances_annual,
        overtime_pay_text=row.overtime_pay_text,
        housing_value=row.housing_value,
        transport_value=row.transport_value,
        other_cash_annual=row.other_cash_annual,
        salary_text_original=row.salary_text_original,
        currency=row.currency,
        requested_start_date=row.requested_start_date,
        requested_remote_policy=row.requested_remote_policy,
        other_request=row.other_request,
        effective_at=row.effective_at,
        notes=row.notes,
        corrects_revision_id=row.corrects_revision_id,
        created_at=row.created_at,
        summary=_summary(offer_management.breakdown_of(row), row.currency)
        or CompensationSummary(currency=row.currency),
    )


def _uplift_out(value) -> UpliftOut:
    if value is None:
        return UpliftOut()
    return UpliftOut(
        absolute=value.absolute,
        percentage=value.percentage,
        initial=value.initial,
        final=value.final,
    )


def _deadline_state(deadline: datetime | None) -> tuple[str, int | None]:
    """Days to the deadline in the reporting timezone, and a display bucket.

    A passed deadline is *displayed* as passed. The offer is never marked
    expired automatically - that stays an explicit human confirmation.
    """
    moment = _utc(deadline)
    if moment is None:
        return ("none", None)
    days = (to_local(moment).date() - local_now().date()).days
    if days < 0:
        return ("past", days)
    if days == 0:
        return ("today", 0)
    if days == 1:
        return ("tomorrow", 1)
    if days <= SOON_DAYS:
        return ("soon", days)
    return ("later", days)


def _offer_out(db: Session, offer: Offer) -> OfferOut:
    """Serialize an offer with its cycle context and derived figures."""
    context = offer_management.cycle_context(db, offer)
    job = offer.job

    company_rev = offer_management.latest_company_revision(offer)
    counter_rev = offer_management.latest_candidate_counter(offer)
    accepted_rev = offer_management.accepted_revision(db, offer)
    negotiation = offer_management.negotiation_summary(offer)
    state, days = _deadline_state(offer.decision_deadline)

    return OfferOut(
        id=offer.id,
        job_id=offer.job_id,
        applied_event_id=offer.applied_event_id,
        interview_process_id=offer.interview_process_id,
        status=offer.status,
        status_label=OFFER_STATUS_LABEL[offer.status],
        currency=offer.currency,
        received_at=offer.received_at,
        decision_deadline=offer.decision_deadline,
        proposed_start_date=offer.proposed_start_date,
        employment_type=offer.employment_type,
        work_location=offer.work_location,
        remote_policy=offer.remote_policy,
        probation_text=offer.probation_text,
        benefits=dict(offer.benefits_json or {}),
        accepted_revision_id=offer.accepted_revision_id,
        declined_revision_id=offer.declined_revision_id,
        decline_reason=offer.decline_reason,
        decline_reason_label=(
            DECLINE_REASON_LABEL.get(offer.decline_reason)
            if offer.decline_reason
            else None
        ),
        decided_at=offer.decided_at,
        notes=offer.notes,
        created_at=offer.created_at,
        updated_at=offer.updated_at,
        revisions=[
            _revision_out(r)
            for r in sorted(offer.revisions, key=lambda r: (r.revision_index, r.id))
        ],
        company=job.company if job else "",
        title=job.title if job else "",
        city=job.city if job else None,
        applied_at=context.applied_at if context else None,
        resume_id=context.resume_id if context else None,
        resume_label=context.resume_label if context else None,
        resume_archived=context.resume_archived if context else False,
        current_company_revision_id=company_rev.id if company_rev else None,
        current_company_summary=_summary(
            offer_management.breakdown_of(company_rev), offer.currency
        ),
        latest_counter_revision_id=counter_rev.id if counter_rev else None,
        latest_counter_summary=_summary(
            offer_management.breakdown_of(counter_rev), offer.currency
        ),
        accepted_summary=_summary(
            offer_management.breakdown_of(accepted_rev), offer.currency
        ),
        negotiation=NegotiationSummary(
            rounds=negotiation.rounds,
            candidate_counters=negotiation.candidate_counters,
            company_revisions=negotiation.company_revisions,
            base=_uplift_out(negotiation.base),
            first_year_guaranteed=_uplift_out(negotiation.first_year_guaranteed),
            first_year_target=_uplift_out(negotiation.first_year_target),
            signing_bonus_gained=negotiation.signing_bonus_gained,
            remote_changed=negotiation.remote_changed,
            start_date_changed=negotiation.start_date_changed,
            has_negotiation_sequence=negotiation.has_negotiation_sequence,
        ),
        days_to_deadline=days,
        deadline_state=state,
    )


# --------------------------------------------------------------------------
# board and lists
# --------------------------------------------------------------------------


@router.get("/offers", response_model=OfferBoardResponse)
def offer_board(db: Session = Depends(get_db)) -> OfferBoardResponse:
    """The Offer page, bucketed by what needs a decision.

    Deadlines are compared in ``REPORT_TIMEZONE`` so "今天截止" means the same
    thing here as everywhere else.
    """
    cfg = get_settings()
    pending: list[OfferOut] = []
    negotiating: list[OfferOut] = []
    accepted: list[OfferOut] = []
    closed: list[OfferOut] = []

    for offer in offer_management.list_offers(db):
        payload = _offer_out(db, offer)
        if offer.status is OfferStatus.accepted:
            accepted.append(payload)
        elif offer.status is OfferStatus.negotiating:
            negotiating.append(payload)
        elif offer.status in {OfferStatus.received, OfferStatus.draft}:
            pending.append(payload)
        else:
            closed.append(payload)

    # Soonest deadline first - that is the thing that needs attention.
    def by_deadline(item: OfferOut):
        # No deadline sorts last, not first - "no rush" is not "most urgent".
        return (
            item.days_to_deadline if item.days_to_deadline is not None else 10_000,
            item.id,
        )

    pending.sort(key=by_deadline)
    negotiating.sort(key=by_deadline)

    legacy = [
        LegacyOfferMilestone(
            job_id=event.job_id,
            event_id=event.id,
            company=event.job.company if event.job else "",
            title=event.job.title if event.job else "",
            occurred_at=event.created_at,
            note=event.notes,
            legacy_salary_text=(event.metadata_json or {}).get("offer_salary"),
        )
        for event in offer_management.legacy_offer_events(db)
    ]

    return OfferBoardResponse(
        timezone=cfg.report_timezone,
        generated_at=datetime.now(timezone.utc),
        pending=pending,
        negotiating=negotiating,
        accepted=accepted,
        closed=closed,
        legacy_offer_events=legacy,
    )


@router.get("/offers/compare", response_model=OfferComparisonResponse)
def compare_offers(
    db: Session = Depends(get_db),
    offer_ids: list[int] = Query(default=[], alias="offer_id"),
) -> OfferComparisonResponse:
    """Side-by-side comparison of 2-4 offers.

    Deliberately produces **no overall winner**. Non-cash factors are shown as
    themselves rather than folded into a score, and offers in different
    currencies are never ranked against each other - v0.9 has no FX model.
    """
    unique = list(dict.fromkeys(offer_ids))
    if len(unique) < 2:
        raise ValidationError("请至少选择两个 Offer 进行比较。", detail={"field": "offer_id"})
    if len(unique) > MAX_COMPARE:
        raise ValidationError(
            f"一次最多比较 {MAX_COMPARE} 个 Offer。",
            detail={"field": "offer_id", "max": MAX_COMPARE},
        )

    rows: list[OfferComparisonRow] = []
    currencies: list[Currency] = []

    for offer_id in unique:
        offer = offer_management.get_offer(db, offer_id)
        payload = _offer_out(db, offer)
        revision = offer_management.accepted_revision(
            db, offer
        ) or offer_management.latest_company_revision(offer)
        breakdown = offer_management.breakdown_of(revision)

        if offer.currency not in currencies:
            currencies.append(offer.currency)

        rows.append(
            OfferComparisonRow(
                offer_id=offer.id,
                job_id=offer.job_id,
                company=payload.company,
                title=payload.title,
                city=payload.city,
                status=offer.status,
                currency=offer.currency,
                base_annual=breakdown.base_annual if breakdown else None,
                first_year_guaranteed_cash=(
                    breakdown.first_year_guaranteed_cash if breakdown else None
                ),
                first_year_target_cash=(
                    breakdown.first_year_target_cash if breakdown else None
                ),
                estimated_first_year_total_comp=(
                    breakdown.estimated_first_year_total_comp if breakdown else None
                ),
                equity_excluded=breakdown.equity_excluded if breakdown else False,
                bonus_target=revision.bonus_target if revision else None,
                signing_bonus=revision.signing_bonus if revision else None,
                stock_value=revision.stock_value if revision else None,
                stock_type=revision.stock_type if revision else None,
                remote_policy=offer.remote_policy,
                work_location=offer.work_location,
                proposed_start_date=offer.proposed_start_date,
                decision_deadline=offer.decision_deadline,
                deadline_state=payload.deadline_state,
                benefits=dict(offer.benefits_json or {}),
                resume_label=payload.resume_label,
            )
        )

    mixed = len(currencies) > 1
    notes: list[str] = []
    if mixed:
        notes.append(
            "所选 Offer 涉及多种币种，金额按原币种分别显示，不做换算或排名 —— "
            "系统不会自行使用任何汇率。"
        )
    if any(row.equity_excluded for row in rows):
        notes.append("部分 Offer 的股权无法折算，未计入可比较总包。")

    return OfferComparisonResponse(
        rows=rows,
        currencies=currencies,
        mixed_currency=mixed,
        message="并列显示各项条件，系统不会给出综合评分或推荐结论。",
        notes=notes,
    )


@router.get("/jobs/{job_id}/offers", response_model=OfferListResponse)
def job_offers(job_id: int, db: Session = Depends(get_db)) -> OfferListResponse:
    """Every offer for a job - a re-applied job legitimately has several."""
    offers = offer_management.list_offers(db, job_id=job_id)
    return OfferListResponse(
        items=[_offer_out(db, o) for o in offers], total=len(offers)
    )


@router.post("/jobs/{job_id}/offers", response_model=OfferActionResponse)
def create_offer(
    job_id: int,
    payload: OfferCreateRequest = Body(...),
    db: Session = Depends(get_db),
) -> OfferActionResponse:
    """Record an offer on one application cycle. Requires confirmation."""
    offer = offer_management.create_offer(db, job_id, payload)
    return OfferActionResponse(
        offer=_offer_out(db, offer),
        message="已记录 Offer",
        job_status=offer.job.status.value if offer.job else None,
    )


@router.get("/offers/{offer_id}", response_model=OfferOut)
def get_offer(offer_id: int, db: Session = Depends(get_db)) -> OfferOut:
    return _offer_out(db, offer_management.get_offer(db, offer_id))


@router.patch("/offers/{offer_id}", response_model=OfferActionResponse)
def update_offer(
    offer_id: int,
    payload: OfferUpdateRequest = Body(...),
    db: Session = Depends(get_db),
) -> OfferActionResponse:
    """Correct offer metadata. Negotiation history is never edited here."""
    offer = offer_management.update_offer(db, offer_id, payload)
    return OfferActionResponse(offer=_offer_out(db, offer), message="已更新")


# --------------------------------------------------------------------------
# negotiation
# --------------------------------------------------------------------------


@router.post("/offers/{offer_id}/revisions", response_model=OfferActionResponse)
def add_revision(
    offer_id: int,
    payload: RevisionCreateRequest = Body(...),
    db: Session = Depends(get_db),
) -> OfferActionResponse:
    """Append a revision. ``source`` decides whether it is an offer or an ask."""
    offer, row = offer_management.add_revision(db, offer_id, payload)
    return OfferActionResponse(
        offer=_offer_out(db, offer),
        message=f"已记录：{REVISION_TYPE_LABEL[row.revision_type]}",
    )


@router.post("/offers/{offer_id}/counter", response_model=OfferActionResponse)
def record_counter(
    offer_id: int,
    payload: CounterRequest = Body(...),
    db: Session = Depends(get_db),
) -> OfferActionResponse:
    """我的谈薪诉求 - recorded, never sent.

    JobAgent stores what you intend to ask for. It messages no one, and this
    never becomes "the current offer".
    """
    offer, _ = offer_management.record_counter(db, offer_id, payload)
    return OfferActionResponse(
        offer=_offer_out(db, offer),
        message="已记录你的谈薪诉求。JobAgent 不会替你发送任何消息。",
    )


# --------------------------------------------------------------------------
# decisions
# --------------------------------------------------------------------------


@router.post("/offers/{offer_id}/accept", response_model=OfferActionResponse)
def accept_offer(
    offer_id: int,
    payload: AcceptOfferRequest = Body(...),
    db: Session = Depends(get_db),
) -> OfferActionResponse:
    """Accept an offer, freezing which revision was accepted."""
    offer = offer_management.accept_offer(db, offer_id, payload)
    return OfferActionResponse(
        offer=_offer_out(db, offer),
        message="已记录：接受该 Offer",
        job_status=offer.job.status.value if offer.job else None,
    )


@router.post("/offers/{offer_id}/decline", response_model=OfferActionResponse)
def decline_offer(
    offer_id: int,
    payload: DeclineOfferRequest = Body(...),
    db: Session = Depends(get_db),
) -> OfferActionResponse:
    """Decline an offer - your decision, never recorded as a rejection."""
    offer = offer_management.decline_offer(db, offer_id, payload)
    return OfferActionResponse(
        offer=_offer_out(db, offer), message="已记录：拒绝该 Offer"
    )


@router.post("/offers/{offer_id}/expire", response_model=OfferActionResponse)
def expire_offer(
    offer_id: int,
    payload: CloseOfferRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> OfferActionResponse:
    """Mark an offer expired.

    Never automatic: a passed deadline is *displayed* as passed, and only you
    can say the offer is actually gone.
    """
    offer = offer_management.close_offer(
        db, offer_id, payload or CloseOfferRequest(), status=OfferStatus.expired
    )
    return OfferActionResponse(offer=_offer_out(db, offer), message="已标记为过期")


@router.post("/offers/{offer_id}/withdraw", response_model=OfferActionResponse)
def withdraw_offer(
    offer_id: int,
    payload: CloseOfferRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> OfferActionResponse:
    """The company withdrew the offer."""
    offer = offer_management.close_offer(
        db, offer_id, payload or CloseOfferRequest(), status=OfferStatus.withdrawn
    )
    return OfferActionResponse(offer=_offer_out(db, offer), message="已记录：对方撤回")


# --------------------------------------------------------------------------
# text parsing
# --------------------------------------------------------------------------


@router.post("/offers/parse-text", response_model=ParsedSalaryOut)
def parse_text(
    payload: ParseOfferTextRequest = Body(...),
) -> ParsedSalaryOut:
    """Read a pasted offer, deterministically.

    Saves nothing and calls no model. What it returns is a *suggestion* for the
    form - the user confirms or corrects every figure, and their entry always
    wins. The original wording is stored alongside whatever they keep.
    """
    parsed = parse_offer_salary(payload.text)
    currency = None
    if parsed.currency:
        try:
            currency = Currency(parsed.currency)
        except ValueError:
            currency = None

    return ParsedSalaryOut(
        base_salary_annual=parsed.base_salary_annual,
        base_salary_monthly=parsed.base_salary_monthly,
        months_per_year=parsed.months_per_year,
        currency=currency,
        confidence=parsed.confidence,
        matched_text=parsed.matched_text,
        notes=parsed.notes,
        message=(
            "以下为自动识别结果，请核对后再保存 —— 你填写的数值始终优先。"
            if parsed.is_usable
            else "未能识别出确定的薪资数字，请手动填写。"
        ),
    )
