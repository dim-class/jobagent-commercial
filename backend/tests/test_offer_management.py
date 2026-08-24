"""Offer lifecycle, negotiation history and decision snapshots (v0.9).

The load-bearing tests:

* a candidate counter is never read as the company's offer;
* ``accepted_revision_id`` freezes what was accepted, so later revisions and
  corrections cannot move it;
* an offer belongs to one application cycle, and the resume credited follows
  from that cycle - never from the active resume.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.core.errors import NotFoundError, ValidationError
from app.models import (
    Currency,
    DeclineReason,
    EquityType,
    EventType,
    JobStatus,
    Offer,
    OfferStatus,
    RemotePolicy,
    RevisionSource,
    RevisionType,
)
from app.schemas.application import OfferRequest, ResetRequest
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
from app.services import application_workflow, offer_management, resume_variants
from app.services.application_cycles import build_cycles, effective_cycle

from tests.test_career_analytics import make_job
from tests.test_resume_variants import apply_with, make_resume

NOW = datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------


def applied_job(db, resume=None, **job_kwargs):
    job = make_job(db, status=JobStatus.new, **job_kwargs)
    apply_with(db, job, resume)
    db.refresh(job)
    return job


def make_offer(db, job, *, initial=None, **kwargs) -> Offer:
    return offer_management.create_offer(
        db,
        job.id,
        OfferCreateRequest(
            confirmed=True,
            initial=CompensationFields(**initial) if initial else None,
            **kwargs,
        ),
    )


def company_revision(db, offer, **comp):
    return offer_management.add_revision(
        db,
        offer.id,
        RevisionCreateRequest(
            revision_type=RevisionType.company_revision,
            source=RevisionSource.company,
            **comp,
        ),
    )


def final_revision(db, offer, **comp):
    return offer_management.add_revision(
        db,
        offer.id,
        RevisionCreateRequest(
            revision_type=RevisionType.final, source=RevisionSource.company, **comp
        ),
    )


def counter(db, offer, **comp):
    return offer_management.record_counter(db, offer.id, CounterRequest(**comp))


# --------------------------------------------------------------------------
# creation
# --------------------------------------------------------------------------


def test_an_offer_belongs_to_the_application_cycle(db):
    resume = make_resume(db, variant_name="DevOps版")
    job = applied_job(db, resume)

    offer = make_offer(db, job, initial={"base_salary_annual": 300_000})

    cycle = effective_cycle(job)
    assert offer.applied_event_id == cycle.applied_event_id
    assert offer.status is OfferStatus.received
    assert len(offer.revisions) == 1
    assert offer.revisions[0].revision_type is RevisionType.initial


def test_creating_an_offer_requires_confirmation(db):
    job = applied_job(db)
    with pytest.raises(ValidationError):
        offer_management.create_offer(db, job.id, OfferCreateRequest(confirmed=False))


def test_a_job_with_no_application_cannot_have_an_offer(db):
    job = make_job(db, status=JobStatus.new)
    with pytest.raises(ValidationError) as excinfo:
        make_offer(db, job)
    assert "还没有投递记录" in str(excinfo.value)


def test_one_cycle_gets_only_one_offer(db):
    job = applied_job(db)
    make_offer(db, job)

    with pytest.raises(ValidationError) as excinfo:
        make_offer(db, job)
    assert "已经记录过 Offer" in str(excinfo.value)


def test_an_offer_can_exist_without_an_interview_process(db):
    """Direct recruiter approaches skip interviews entirely."""
    job = applied_job(db)
    offer = make_offer(db, job)
    assert offer.interview_process_id is None


def test_an_offer_links_to_the_cycles_interview_process(db):
    from app.schemas.interview import ProcessCreateRequest
    from app.services import interview_pipeline

    job = applied_job(db)
    process = interview_pipeline.create_process(db, job.id, ProcessCreateRequest())

    offer = make_offer(db, job)
    assert offer.interview_process_id == process.id


def test_an_offer_can_open_with_no_numbers_yet(db):
    job = applied_job(db)
    offer = make_offer(db, job)
    assert offer.revisions == []
    assert offer_management.latest_company_revision(offer) is None


def test_the_offers_currency_is_denormalized_onto_its_revisions(db):
    job = applied_job(db)
    offer = make_offer(
        db, job, currency=Currency.JPY, initial={"base_salary_annual": 8_000_000}
    )
    assert offer.revisions[0].currency is Currency.JPY


def test_the_original_wording_is_preserved(db):
    job = applied_job(db)
    offer = make_offer(
        db,
        job,
        initial={
            "base_salary_annual": 300_000,
            "salary_text_original": "年薪30万，另有年终奖",
        },
    )
    assert offer.revisions[0].salary_text_original == "年薪30万，另有年终奖"


# --------------------------------------------------------------------------
# the company-offer / candidate-ask distinction
# --------------------------------------------------------------------------


def test_a_candidate_counter_is_not_the_current_company_offer(db):
    """The distinction the whole negotiation view depends on."""
    job = applied_job(db)
    offer = make_offer(db, job, initial={"base_salary_annual": 300_000})
    counter(db, offer, base_salary_annual=350_000)

    offer = offer_management.get_offer(db, offer.id)
    company = offer_management.latest_company_revision(offer)
    ask = offer_management.latest_candidate_counter(offer)

    assert company.base_salary_annual == 300_000, "still what the company offered"
    assert ask.base_salary_annual == 350_000
    assert company.id != ask.id


def test_the_current_company_offer_follows_company_revisions(db):
    job = applied_job(db)
    offer = make_offer(db, job, initial={"base_salary_annual": 300_000})
    counter(db, offer, base_salary_annual=350_000)
    company_revision(db, offer, base_salary_annual=330_000, signing_bonus=20_000)

    offer = offer_management.get_offer(db, offer.id)
    company = offer_management.latest_company_revision(offer)
    assert company.base_salary_annual == 330_000
    assert offer_management.latest_candidate_counter(offer).base_salary_annual == 350_000


def test_the_spec_negotiation_sequence(db):
    """Initial 300K -> counter 350K -> company 330K -> final 340K."""
    job = applied_job(db)
    offer = make_offer(
        db, job, initial={"base_salary_annual": 300_000, "bonus_target": 50_000}
    )
    counter(db, offer, base_salary_annual=350_000)
    company_revision(
        db, offer, base_salary_annual=330_000, signing_bonus=20_000, bonus_target=50_000
    )
    final_revision(
        db, offer, base_salary_annual=340_000, signing_bonus=20_000, bonus_target=60_000
    )

    offer = offer_management.get_offer(db, offer.id)
    assert [r.revision_type for r in offer.revisions] == [
        RevisionType.initial,
        RevisionType.candidate_counter,
        RevisionType.company_revision,
        RevisionType.final,
    ]
    current = offer_management.latest_company_revision(offer)
    assert current.base_salary_annual == 340_000
    assert current.revision_type is RevisionType.final

    initial = offer_management.initial_company_revision(offer)
    assert initial.base_salary_annual == 300_000


def test_a_candidate_counter_must_declare_a_candidate_source(db):
    from pydantic import ValidationError as PydanticError

    with pytest.raises(PydanticError):
        RevisionCreateRequest(
            revision_type=RevisionType.candidate_counter, source=RevisionSource.company
        )


def test_a_candidate_source_cannot_masquerade_as_a_company_revision(db):
    from pydantic import ValidationError as PydanticError

    with pytest.raises(PydanticError):
        RevisionCreateRequest(
            revision_type=RevisionType.company_revision, source=RevisionSource.candidate
        )


def test_a_counter_records_what_was_asked_but_sends_nothing(db):
    job = applied_job(db)
    offer = make_offer(db, job, initial={"base_salary_annual": 300_000})

    _, row = counter(
        db,
        offer,
        base_salary_annual=350_000,
        requested_start_date=date(2026, 10, 1),
        requested_remote_policy=RemotePolicy.remote,
        other_request="希望每周两天远程",
    )

    assert row.source is RevisionSource.candidate
    assert row.is_company_offer is False
    assert row.requested_remote_policy is RemotePolicy.remote
    assert row.other_request == "希望每周两天远程"


def test_a_revision_moves_the_offer_into_negotiating(db):
    job = applied_job(db)
    offer = make_offer(db, job, initial={"base_salary_annual": 300_000})
    assert offer.status is OfferStatus.received

    offer, _ = counter(db, offer, base_salary_annual=350_000)
    assert offer.status is OfferStatus.negotiating


def test_revisions_are_appended_never_overwritten(db):
    job = applied_job(db)
    offer = make_offer(db, job, initial={"base_salary_annual": 300_000})
    company_revision(db, offer, base_salary_annual=330_000)

    offer = offer_management.get_offer(db, offer.id)
    assert len(offer.revisions) == 2
    assert offer.revisions[0].base_salary_annual == 300_000, "history is intact"
    assert [r.revision_index for r in offer.revisions] == [1, 2]


def test_a_correction_appends_rather_than_editing(db):
    job = applied_job(db)
    offer = make_offer(db, job, initial={"base_salary_annual": 300_000})
    original = offer.revisions[0]

    offer, corrected = offer_management.add_revision(
        db,
        offer.id,
        RevisionCreateRequest(
            revision_type=RevisionType.company_revision,
            source=RevisionSource.company,
            base_salary_annual=320_000,
            corrects_revision_id=original.id,
        ),
    )

    db.refresh(original)
    assert original.base_salary_annual == 300_000, "the original row is untouched"
    assert corrected.corrects_revision_id == original.id

    db.refresh(offer.job)
    assert EventType.offer_revision_corrected in {
        e.event_type for e in offer.job.events
    }


def test_correcting_an_unknown_revision_is_a_clean_404(db):
    job = applied_job(db)
    offer = make_offer(db, job)
    with pytest.raises(NotFoundError):
        offer_management.add_revision(
            db,
            offer.id,
            RevisionCreateRequest(corrects_revision_id=999_999),
        )


# --------------------------------------------------------------------------
# negotiation summary
# --------------------------------------------------------------------------


def test_uplift_compares_company_figures_only(db):
    job = applied_job(db)
    offer = make_offer(db, job, initial={"base_salary_annual": 300_000})
    counter(db, offer, base_salary_annual=500_000)
    company_revision(db, offer, base_salary_annual=330_000)

    offer = offer_management.get_offer(db, offer.id)
    summary = offer_management.negotiation_summary(offer)

    assert summary.base.initial == 300_000
    assert summary.base.final == 330_000, "the 500K ask is not an offer"
    assert summary.base.absolute == 30_000
    assert summary.base.percentage == pytest.approx(0.1)


def test_a_single_company_revision_yields_no_uplift(db):
    job = applied_job(db)
    offer = make_offer(db, job, initial={"base_salary_annual": 300_000})

    summary = offer_management.negotiation_summary(offer)
    assert summary.base is None or summary.base.absolute is None


def test_uplift_is_null_when_a_figure_is_missing(db):
    job = applied_job(db)
    offer = make_offer(db, job, initial={"bonus_target": 50_000})
    company_revision(db, offer, base_salary_annual=330_000)

    offer = offer_management.get_offer(db, offer.id)
    summary = offer_management.negotiation_summary(offer)
    assert summary.base.absolute is None, "no initial base means no uplift, not 100%"


def test_a_negotiation_sequence_needs_a_counter_before_a_revision(db):
    """Without that ordering, a change is a change - not evidence of negotiating."""
    job = applied_job(db)
    offer = make_offer(db, job, initial={"base_salary_annual": 300_000})
    company_revision(db, offer, base_salary_annual=330_000)

    offer = offer_management.get_offer(db, offer.id)
    assert offer_management.negotiation_summary(offer).has_negotiation_sequence is False

    counter(db, offer, base_salary_annual=350_000)
    offer = offer_management.get_offer(db, offer.id)
    final_revision(db, offer, base_salary_annual=340_000)

    offer = offer_management.get_offer(db, offer.id)
    assert offer_management.negotiation_summary(offer).has_negotiation_sequence is True


def test_signing_bonus_gained_is_tracked(db):
    job = applied_job(db)
    offer = make_offer(db, job, initial={"base_salary_annual": 300_000})
    company_revision(db, offer, base_salary_annual=300_000, signing_bonus=20_000)

    offer = offer_management.get_offer(db, offer.id)
    assert offer_management.negotiation_summary(offer).signing_bonus_gained == 20_000


# --------------------------------------------------------------------------
# decisions
# --------------------------------------------------------------------------


def test_accepting_requires_confirmation(db):
    job = applied_job(db)
    offer = make_offer(db, job, initial={"base_salary_annual": 300_000})
    with pytest.raises(ValidationError):
        offer_management.accept_offer(db, offer.id, AcceptOfferRequest(confirmed=False))


def test_accepting_freezes_the_accepted_revision(db):
    job = applied_job(db)
    offer = make_offer(db, job, initial={"base_salary_annual": 300_000})
    company_revision(db, offer, base_salary_annual=330_000)
    offer = offer_management.get_offer(db, offer.id)
    expected = offer_management.latest_company_revision(offer)

    offer = offer_management.accept_offer(db, offer.id, AcceptOfferRequest(confirmed=True))

    assert offer.status is OfferStatus.accepted
    assert offer.accepted_revision_id == expected.id
    assert offer.decided_at is not None


def test_a_later_revision_does_not_move_the_accepted_compensation(db):
    """The point of ``accepted_revision_id``."""
    job = applied_job(db)
    offer = make_offer(db, job, initial={"base_salary_annual": 300_000})
    offer = offer_management.accept_offer(db, offer.id, AcceptOfferRequest(confirmed=True))
    frozen_id = offer.accepted_revision_id

    # A correction entered later must not rewrite the past decision. The offer
    # is closed, so a revision has to be forced in directly.
    from app.models import OfferRevision

    db.add(
        OfferRevision(
            offer_id=offer.id,
            revision_index=99,
            revision_type=RevisionType.company_revision,
            source=RevisionSource.company,
            base_salary_annual=999_000,
            currency=offer.currency,
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()

    offer = offer_management.get_offer(db, offer.id)
    accepted = offer_management.accepted_revision(db, offer)
    assert offer.accepted_revision_id == frozen_id
    assert accepted.base_salary_annual == 300_000
    assert offer_management.latest_company_revision(offer).base_salary_annual == 999_000, (
        "the latest revision really did change - the frozen one did not"
    )


def test_you_cannot_accept_your_own_counter(db):
    """You accept what the company offered, not what you asked for."""
    job = applied_job(db)
    offer = make_offer(db, job, initial={"base_salary_annual": 300_000})
    offer, ask = counter(db, offer, base_salary_annual=350_000)

    with pytest.raises(ValidationError) as excinfo:
        offer_management.accept_offer(
            db, offer.id, AcceptOfferRequest(confirmed=True, revision_id=ask.id)
        )
    assert "我方诉求" in str(excinfo.value)


def test_accepting_defaults_to_the_company_offer_not_the_latest_revision(db):
    job = applied_job(db)
    offer = make_offer(db, job, initial={"base_salary_annual": 300_000})
    company_revision(db, offer, base_salary_annual=330_000)
    offer = offer_management.get_offer(db, offer.id)
    counter(db, offer, base_salary_annual=350_000)

    offer = offer_management.accept_offer(db, offer.id, AcceptOfferRequest(confirmed=True))

    accepted = offer_management.accepted_revision(db, offer)
    assert accepted.base_salary_annual == 330_000, "not the 350K ask"


def test_declining_requires_confirmation(db):
    job = applied_job(db)
    offer = make_offer(db, job)
    with pytest.raises(ValidationError):
        offer_management.decline_offer(db, offer.id, DeclineOfferRequest(confirmed=False))


def test_declining_records_a_reason_and_a_snapshot(db):
    job = applied_job(db)
    offer = make_offer(db, job, initial={"base_salary_annual": 300_000})
    expected = offer.revisions[0]

    offer = offer_management.decline_offer(
        db,
        offer.id,
        DeclineOfferRequest(confirmed=True, reason=DeclineReason.salary),
    )

    assert offer.status is OfferStatus.declined
    assert offer.decline_reason is DeclineReason.salary
    assert offer.declined_revision_id == expected.id


def test_declining_is_not_an_employer_rejection(db):
    job = applied_job(db)
    offer = make_offer(db, job, initial={"base_salary_annual": 300_000})

    offer_management.decline_offer(
        db, offer.id, DeclineOfferRequest(confirmed=True, reason=DeclineReason.location)
    )

    db.refresh(job)
    assert job.status is not JobStatus.rejected, "your decision is not their rejection"
    assert EventType.offer_declined in {e.event_type for e in job.events}
    assert EventType.rejected not in {e.event_type for e in job.events}


def test_a_decided_offer_cannot_be_decided_again(db):
    job = applied_job(db)
    offer = make_offer(db, job, initial={"base_salary_annual": 300_000})
    offer_management.accept_offer(db, offer.id, AcceptOfferRequest(confirmed=True))

    with pytest.raises(ValidationError):
        offer_management.decline_offer(
            db, offer.id, DeclineOfferRequest(confirmed=True)
        )


def test_a_closed_offer_cannot_gain_revisions(db):
    job = applied_job(db)
    offer = make_offer(db, job, initial={"base_salary_annual": 300_000})
    offer_management.accept_offer(db, offer.id, AcceptOfferRequest(confirmed=True))

    with pytest.raises(ValidationError):
        company_revision(db, offer, base_salary_annual=400_000)


def test_expiring_is_explicit_never_automatic(db):
    """A passed deadline is displayed; only a human says the offer is gone."""
    job = applied_job(db)
    offer = make_offer(
        db,
        job,
        decision_deadline=NOW - timedelta(days=5),
        initial={"base_salary_annual": 300_000},
    )
    assert offer.status is OfferStatus.received, "a passed deadline changed nothing"

    offer = offer_management.close_offer(
        db, offer.id, CloseOfferRequest(confirmed=True), status=OfferStatus.expired
    )
    assert offer.status is OfferStatus.expired


def test_withdrawing_needs_confirmation(db):
    job = applied_job(db)
    offer = make_offer(db, job)
    with pytest.raises(ValidationError):
        offer_management.close_offer(
            db, offer.id, CloseOfferRequest(), status=OfferStatus.withdrawn
        )


# --------------------------------------------------------------------------
# events
# --------------------------------------------------------------------------


def test_events_carry_references_not_compensation(db):
    """The trail is a milestone log, not a second copy of the offer."""
    job = applied_job(db)
    offer = make_offer(db, job, initial={"base_salary_annual": 300_000})
    company_revision(db, offer, base_salary_annual=330_000)
    offer_management.accept_offer(db, offer.id, AcceptOfferRequest(confirmed=True))

    db.refresh(job)
    for event in job.events:
        payload = str(event.metadata_json or {})
        assert "300000" not in payload
        assert "330000" not in payload
        assert "base_salary" not in payload

    received = next(e for e in job.events if e.event_type is EventType.offer_received)
    assert received.metadata_json["offer_id"] == offer.id
    revised = next(e for e in job.events if e.event_type is EventType.offer_revised)
    assert "offer_revision_id" in revised.metadata_json


def test_the_milestone_events_are_emitted(db):
    job = applied_job(db)
    offer = make_offer(db, job, initial={"base_salary_annual": 300_000})
    counter(db, offer, base_salary_annual=350_000)
    offer = offer_management.get_offer(db, offer.id)
    company_revision(db, offer, base_salary_annual=330_000)
    offer_management.accept_offer(db, offer.id, AcceptOfferRequest(confirmed=True))

    db.refresh(job)
    kinds = {e.event_type for e in job.events}
    assert EventType.offer_received in kinds
    assert EventType.offer_countered in kinds
    assert EventType.offer_revised in kinds
    assert EventType.offer_accepted in kinds


# --------------------------------------------------------------------------
# the v0.4 记录Offer path
# --------------------------------------------------------------------------


def test_the_legacy_action_now_creates_a_structured_offer(db):
    """There is no parallel "offer happened" path any more."""
    job = applied_job(db)
    application_workflow.record_offer(db, job.id, OfferRequest(salary_text="35k"))

    offer = offer_management.offer_for_job(db, job.id)
    assert offer is not None
    assert offer.revisions[0].salary_text_original == "35k"
    db.refresh(job)
    assert job.status is JobStatus.offer


def test_the_legacy_action_does_not_create_a_second_offer(db):
    job = applied_job(db)
    make_offer(db, job, initial={"base_salary_annual": 300_000})

    application_workflow.record_offer(db, job.id, OfferRequest(salary_text="35k"))

    assert len(offer_management.list_offers(db, job_id=job.id)) == 1


def test_the_legacy_action_closes_the_interview_process(db):
    from app.models import InterviewProcessStatus
    from app.schemas.interview import ProcessCreateRequest
    from app.services import interview_pipeline

    job = applied_job(db)
    process = interview_pipeline.create_process(db, job.id, ProcessCreateRequest())
    application_workflow.record_offer(db, job.id, OfferRequest())

    process = interview_pipeline.get_process(db, process.id)
    assert process.status is InterviewProcessStatus.offer


# --------------------------------------------------------------------------
# attribution
# --------------------------------------------------------------------------


def test_the_offer_carries_the_cycles_resume(db):
    resume = make_resume(db, variant_name="DevOps版")
    job = applied_job(db, resume)
    offer = make_offer(db, job)

    context = offer_management.cycle_context(db, offer)
    assert context.resume_id == resume.id
    assert context.resume_label == "DevOps版"


def test_reset_then_reapply_attributes_the_offer_to_cycle_two(db):
    cloud = make_resume(db, variant_name="Cloud版")
    devops = make_resume(db, variant_name="DevOps版")
    job = applied_job(db, cloud)
    application_workflow.reset_status(db, job.id, ResetRequest())
    apply_with(db, job, devops)
    db.refresh(job)

    offer = make_offer(db, job, initial={"base_salary_annual": 300_000})

    context = offer_management.cycle_context(db, offer)
    assert context.resume_id == devops.id
    assert offer.applied_event_id == build_cycles(job)[1].applied_event_id


def test_switching_the_active_resume_does_not_move_the_offer(db):
    cloud = make_resume(db, variant_name="Cloud版", active=True)
    job = applied_job(db, cloud)
    offer = make_offer(db, job)

    devops = make_resume(db, variant_name="DevOps版")
    cloud.is_active = False
    devops.is_active = True
    db.commit()

    context = offer_management.cycle_context(db, offer)
    assert context.resume_id == cloud.id, "attribution is frozen at application time"


def test_an_archived_resume_stays_visible_on_its_offer(db):
    resume = make_resume(db, variant_name="旧版")
    job = applied_job(db, resume)
    offer = make_offer(db, job)
    resume_variants.archive(db, resume.id)

    context = offer_management.cycle_context(db, offer)
    assert context.resume_id == resume.id
    assert context.resume_archived is True


def test_offer_for_job_returns_only_the_current_cycles_offer(db):
    cloud = make_resume(db, variant_name="Cloud版")
    devops = make_resume(db, variant_name="DevOps版")
    job = applied_job(db, cloud)
    first_cycle = build_cycles(job)[0]
    old = offer_management.create_offer(
        db,
        job.id,
        OfferCreateRequest(confirmed=True, applied_event_id=first_cycle.applied_event_id),
    )
    application_workflow.reset_status(db, job.id, ResetRequest())
    apply_with(db, job, devops)
    db.refresh(job)

    assert offer_management.offer_for_job(db, job.id) is None
    current = make_offer(db, job)
    assert offer_management.offer_for_job(db, job.id).id == current.id
    assert current.id != old.id


# --------------------------------------------------------------------------
# metadata edits and legacy
# --------------------------------------------------------------------------


def test_offer_metadata_can_be_corrected_without_touching_history(db):
    job = applied_job(db)
    offer = make_offer(db, job, initial={"base_salary_annual": 300_000})

    offer = offer_management.update_offer(
        db,
        offer.id,
        OfferUpdateRequest(
            decision_deadline=NOW + timedelta(days=7),
            proposed_start_date=date(2026, 11, 1),
            remote_policy=RemotePolicy.hybrid,
            benefits={"paid_leave": "15天", "visa_support": True},
        ),
    )

    assert offer.remote_policy is RemotePolicy.hybrid
    assert offer.benefits_json["paid_leave"] == "15天"
    assert offer.revisions[0].base_salary_annual == 300_000, "history untouched"


def test_benefits_are_never_given_a_monetary_value(db):
    job = applied_job(db)
    offer = make_offer(
        db,
        job,
        initial={"base_salary_annual": 300_000},
        benefits={"housing_support": "提供宿舍"},
    )
    breakdown = offer_management.breakdown_of(offer.revisions[0])
    assert breakdown.first_year_guaranteed_cash == 300_000, "a benefit is not cash"


def test_a_pre_v09_offer_event_is_not_converted(db):
    """A v0.4 event carries at most a salary string - never a structured offer."""
    from tests.test_career_analytics import add_event

    job = applied_job(db)
    add_event(db, job, EventType.offer, at=NOW - timedelta(days=10))
    db.refresh(job)

    legacy = offer_management.legacy_offer_events(db)
    assert [e.job_id for e in legacy] == [job.id]
    assert offer_management.offer_for_job(db, job.id) is None


def test_a_job_with_a_real_offer_reports_no_legacy_event(db):
    job = applied_job(db)
    make_offer(db, job, initial={"base_salary_annual": 300_000})
    assert offer_management.legacy_offer_events(db) == []


def test_deleting_a_job_with_an_accepted_offer_works(db):
    """Regression guard.

    ``offers.accepted_revision_id`` is RESTRICT so a snapshot cannot go
    dangling. Deleting the revisions from the ORM before the offer row tripped
    that constraint, which made ``DELETE /api/jobs/{id}`` fail on any job that
    had an accepted offer.
    """
    from app.models import Job, Offer, OfferRevision

    job = applied_job(db)
    offer = make_offer(db, job, initial={"base_salary_annual": 300_000})
    company_revision(db, offer, base_salary_annual=330_000)
    offer = offer_management.get_offer(db, offer.id)
    offer_management.accept_offer(db, offer.id, AcceptOfferRequest(confirmed=True))

    offer_id = offer.id
    db.delete(db.get(Job, job.id))
    db.commit()

    assert db.get(Offer, offer_id) is None
    assert db.query(OfferRevision).filter_by(offer_id=offer_id).count() == 0


def test_deleting_a_job_over_http_works_with_an_offer(client, db):
    from tests.test_offer_api import applied, create

    job = applied(client, db)
    offer = create(client, job.id, initial={"base_salary_annual": 300000})
    client.post(f"/api/offers/{offer['id']}/accept", json={"confirmed": True})

    response = client.delete(f"/api/jobs/{job.id}")
    assert response.status_code == 200, response.text
