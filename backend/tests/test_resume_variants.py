"""Resume variants and application-time attribution (v0.7).

The load-bearing tests here are the ones about *history*: an application's
resume is whatever the human recorded at the time, and activating a different
variant later must never move a past outcome from one variant to another.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.errors import NotFoundError, ValidationError
from app.models import (
    ApplicationEvent,
    EventType,
    JobStatus,
    Resume,
    ResumeUsage,
)
from app.schemas.application import (
    AttributeResumeRequest,
    MarkAppliedRequest,
    ResetRequest,
)
from app.services import application_workflow, resume_variants
from app.services.application_cycles import build_cycles, effective_cycle

from tests.test_career_analytics import NOW, add_event, make_job


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------


def after_applying() -> datetime:
    """A moment guaranteed to fall after an ``applied`` event just recorded.

    ``mark_applied`` stamps the real clock, so a fixed constant only works
    while the wall clock happens to sit before it - which made these tests
    start failing at a particular time of day.
    """
    return datetime.now(timezone.utc) + timedelta(minutes=1)


def make_resume(
    db,
    *,
    variant_name: str = "Cloud版",
    variant_group: str | None = "cloud",
    active: bool = False,
    archived: bool = False,
    skills: list[str] | None = None,
) -> Resume:
    make_resume.counter = getattr(make_resume, "counter", 0) + 1
    n = make_resume.counter
    resume = Resume(
        filename=f"resume-{n}.pdf",
        file_type="pdf",
        content_hash=f"resume-hash-{n:06d}",
        raw_text=f"简历内容 {n}",
        parsed_profile_json={"skills": skills or ["AWS", "Linux"]},
        is_active=active,
        variant_name=variant_name,
        variant_group=variant_group,
        archived_at=datetime.now(timezone.utc) if archived else None,
    )
    db.add(resume)
    db.commit()
    db.refresh(resume)
    return resume


def apply_with(db, job, resume: Resume | None, *, usage: ResumeUsage | None = None):
    payload = MarkAppliedRequest(
        confirmed=True,
        resume_id=resume.id if resume else None,
        resume_usage=usage or (ResumeUsage.used if resume else ResumeUsage.unknown),
    )
    return application_workflow.mark_applied(db, job.id, payload)


# --------------------------------------------------------------------------
# variant metadata
# --------------------------------------------------------------------------


def test_a_resume_row_is_a_variant(db):
    resume = make_resume(db, variant_name="DevOps版", variant_group="devops")

    assert resume.display_name == "DevOps版"
    assert resume.variant_group == "devops"
    assert resume.archived is False


def test_an_unnamed_variant_falls_back_to_its_filename(db):
    resume = make_resume(db, variant_name=None)
    assert resume.display_name == resume.filename


def test_renaming_a_variant_leaves_its_content_alone(db):
    resume = make_resume(db, variant_name="Cloud版")
    original_text = resume.raw_text

    renamed = resume_variants.rename(db, resume.id, variant_name="Cloud版 v2")

    assert renamed.display_name == "Cloud版 v2"
    assert renamed.raw_text == original_text, "renaming is metadata, never content"


def test_a_variant_name_has_a_length_limit(db):
    resume = make_resume(db)
    with pytest.raises(ValidationError):
        resume_variants.rename(db, resume.id, variant_name="超长" * 200)


# --------------------------------------------------------------------------
# clone
# --------------------------------------------------------------------------


def test_cloning_creates_a_child_without_touching_the_original(db):
    source = make_resume(db, variant_name="Cloud版", active=True)

    clone = resume_variants.clone(db, source.id, variant_name="Cloud版 v2")

    assert clone.id != source.id
    assert clone.parent_resume_id == source.id
    assert clone.display_name == "Cloud版 v2"
    assert clone.raw_text == source.raw_text

    db.refresh(source)
    assert source.display_name == "Cloud版", "the original is untouched"
    assert source.is_active is True


def test_a_clone_is_not_activated(db):
    source = make_resume(db, active=True)
    clone = resume_variants.clone(db, source.id)

    assert clone.is_active is False, "choosing the analysis resume stays explicit"
    db.refresh(source)
    assert source.is_active is True


def test_a_clone_gets_a_default_name(db):
    source = make_resume(db, variant_name="基础设施版")
    clone = resume_variants.clone(db, source.id)
    assert "基础设施版" in clone.display_name


def test_cloning_an_unknown_resume_is_a_clean_404(db):
    with pytest.raises(NotFoundError):
        resume_variants.clone(db, 9999)


# --------------------------------------------------------------------------
# archive
# --------------------------------------------------------------------------


def test_archiving_hides_a_variant_from_the_default_list(db):
    keep = make_resume(db, variant_name="DevOps版")
    retire = make_resume(db, variant_name="旧版")

    resume_variants.archive(db, retire.id)

    visible = [r.id for r in resume_variants.list_resumes(db)]
    everything = [r.id for r in resume_variants.list_resumes(db, include_archived=True)]
    assert visible == [keep.id]
    assert set(everything) == {keep.id, retire.id}


def test_the_active_analysis_resume_cannot_be_archived(db):
    resume = make_resume(db, active=True)
    with pytest.raises(ValidationError):
        resume_variants.archive(db, resume.id)


def test_archiving_is_idempotent(db):
    resume = make_resume(db)
    first = resume_variants.archive(db, resume.id)
    second = resume_variants.archive(db, resume.id)
    assert first.archived_at == second.archived_at


def test_unarchiving_restores_a_variant(db):
    resume = make_resume(db)
    resume_variants.archive(db, resume.id)
    restored = resume_variants.unarchive(db, resume.id)
    assert restored.archived is False


def test_an_archived_variant_cannot_be_used_for_a_new_application(db):
    resume = make_resume(db, variant_name="旧版")
    resume_variants.archive(db, resume.id)
    job = make_job(db, status=JobStatus.new)

    with pytest.raises(ValidationError) as excinfo:
        apply_with(db, job, resume)
    assert "已归档" in str(excinfo.value)


def test_an_archived_variant_keeps_its_history(db):
    """Archiving retires a variant; it does not erase what it achieved."""
    resume = make_resume(db, variant_name="旧版")
    job = make_job(db, status=JobStatus.new)
    apply_with(db, job, resume)

    resume_variants.archive(db, resume.id)

    db.refresh(job)
    cycle = effective_cycle(job)
    assert cycle.resume_id == resume.id
    assert resume_variants.referencing_application_count(db, resume.id) == 1


# --------------------------------------------------------------------------
# the two "current resume" concepts
# --------------------------------------------------------------------------


def test_the_active_analysis_resume_is_not_the_applied_resume(db):
    """The whole point of v0.7: these are different questions."""
    analysis_resume = make_resume(db, variant_name="Cloud版", active=True)
    submitted = make_resume(db, variant_name="DevOps版")
    job = make_job(db, status=JobStatus.new)

    apply_with(db, job, submitted)

    db.refresh(job)
    cycle = effective_cycle(job)
    assert cycle.resume_id == submitted.id
    assert cycle.resume_id != analysis_resume.id


def test_switching_the_active_resume_never_rewrites_history(db):
    """August: applied with A. Later: activate B. September: interview.

    That interview belongs to A, permanently.
    """
    resume_a = make_resume(db, variant_name="Cloud版", active=True)
    job = make_job(db, status=JobStatus.new)
    apply_with(db, job, resume_a)

    # The user switches their analysis resume weeks later.
    resume_b = make_resume(db, variant_name="DevOps版")
    resume_a.is_active = False
    resume_b.is_active = True
    db.commit()

    db.refresh(job)
    add_event(db, job, EventType.interview, at=after_applying())
    db.refresh(job)

    cycle = effective_cycle(job)
    assert cycle.interviewed is True
    assert cycle.resume_id == resume_a.id, "the interview belongs to the resume actually sent"
    assert cycle.resume_id != resume_b.id


def test_deleting_nothing_is_the_point_a_referenced_resume_stays(db):
    resume = make_resume(db)
    job = make_job(db, status=JobStatus.new)
    apply_with(db, job, resume)

    assert resume_variants.referencing_application_count(db, resume.id) == 1
    assert db.get(Resume, resume.id) is not None


# --------------------------------------------------------------------------
# mark applied
# --------------------------------------------------------------------------


def test_mark_applied_records_the_resume_and_its_name(db):
    resume = make_resume(db, variant_name="DevOps版")
    job = make_job(db, status=JobStatus.new)

    outcome = apply_with(db, job, resume)

    assert outcome.event.resume_id == resume.id
    assert outcome.event.metadata_json["resume_variant_name"] == "DevOps版"
    assert outcome.event.metadata_json["resume_usage"] == "used"


def test_mark_applied_without_a_resume_is_unknown_not_the_active_one(db):
    """Silence is recorded as silence. It is never filled in from is_active."""
    active = make_resume(db, variant_name="Cloud版", active=True)
    job = make_job(db, status=JobStatus.new)

    application_workflow.mark_applied(db, job.id, MarkAppliedRequest(confirmed=True))

    db.refresh(job)
    cycle = effective_cycle(job)
    assert cycle.resume_id is None
    assert cycle.resume_usage is ResumeUsage.unknown
    assert cycle.resume_id != active.id


def test_an_application_can_record_that_no_resume_was_sent(db):
    make_resume(db, active=True)
    job = make_job(db, status=JobStatus.new)

    application_workflow.mark_applied(
        db, job.id, MarkAppliedRequest(confirmed=True, resume_usage=ResumeUsage.no_resume)
    )

    db.refresh(job)
    cycle = effective_cycle(job)
    assert cycle.resume_usage is ResumeUsage.no_resume
    assert cycle.resume_id is None
    assert cycle.resume_attributed is True, "'no resume' is an answer, not a gap"


def test_no_resume_is_distinct_from_unknown(db):
    job_a = make_job(db, status=JobStatus.new)
    job_b = make_job(db, status=JobStatus.new)

    application_workflow.mark_applied(
        db, job_a.id, MarkAppliedRequest(confirmed=True, resume_usage=ResumeUsage.no_resume)
    )
    application_workflow.mark_applied(db, job_b.id, MarkAppliedRequest(confirmed=True))

    db.refresh(job_a)
    db.refresh(job_b)
    assert effective_cycle(job_a).resume_attributed is True
    assert effective_cycle(job_b).resume_attributed is False


def test_marking_applied_with_an_unknown_resume_is_rejected(db):
    job = make_job(db, status=JobStatus.new)
    with pytest.raises(NotFoundError):
        application_workflow.mark_applied(
            db, job.id, MarkAppliedRequest(confirmed=True, resume_id=9999)
        )


def test_mark_applied_still_requires_confirmation(db):
    resume = make_resume(db)
    job = make_job(db, status=JobStatus.new)
    with pytest.raises(ValidationError):
        application_workflow.mark_applied(
            db, job.id, MarkAppliedRequest(confirmed=False, resume_id=resume.id)
        )


# --------------------------------------------------------------------------
# re-application
# --------------------------------------------------------------------------


def test_reset_then_reapply_can_use_a_different_resume(db):
    """Cycle 1 with A, withdrawn. Cycle 2 with B, which gets the interview."""
    resume_a = make_resume(db, variant_name="Cloud版")
    resume_b = make_resume(db, variant_name="DevOps版")
    job = make_job(db, status=JobStatus.new)

    apply_with(db, job, resume_a)
    application_workflow.reset_status(db, job.id, ResetRequest())
    apply_with(db, job, resume_b)
    db.refresh(job)
    moment = after_applying()
    add_event(db, job, EventType.replied, at=moment)
    add_event(db, job, EventType.interview, at=moment)
    db.refresh(job)

    cycles = build_cycles(job)
    assert len(cycles) == 2
    assert cycles[0].resume_id == resume_a.id and cycles[0].superseded is True
    assert cycles[1].resume_id == resume_b.id

    effective = effective_cycle(job)
    assert effective.resume_id == resume_b.id
    assert effective.replied and effective.interviewed
    assert cycles[0].replied is False, "cycle 1's record stays empty"


def test_each_cycle_keeps_its_own_resume(db):
    resume_a = make_resume(db, variant_name="Cloud版")
    resume_b = make_resume(db, variant_name="DevOps版")
    job = make_job(db, status=JobStatus.new)

    apply_with(db, job, resume_a)
    application_workflow.reset_status(db, job.id, ResetRequest())
    apply_with(db, job, resume_b)
    db.refresh(job)

    assert [c.resume_id for c in build_cycles(job)] == [resume_a.id, resume_b.id]


def test_an_offer_is_attributed_to_the_cycle_that_earned_it(db):
    resume_a = make_resume(db, variant_name="Cloud版")
    resume_b = make_resume(db, variant_name="DevOps版")
    job = make_job(db, status=JobStatus.new)

    apply_with(db, job, resume_a)
    application_workflow.reset_status(db, job.id, ResetRequest())
    apply_with(db, job, resume_b)
    db.refresh(job)
    add_event(db, job, EventType.offer, at=after_applying())
    db.refresh(job)

    cycle = effective_cycle(job)
    assert cycle.offered is True
    assert cycle.resume_id == resume_b.id


# --------------------------------------------------------------------------
# historical attribution
# --------------------------------------------------------------------------


def legacy_application(db, job, *, days_ago: float = 20):
    """An application recorded before v0.7 - no resume on the event at all."""
    add_event(db, job, EventType.applied, at=NOW - timedelta(days=days_ago))
    db.refresh(job)
    return job


def test_pre_v07_applications_stay_unknown(db):
    make_resume(db, variant_name="Cloud版", active=True)
    job = legacy_application(db, make_job(db, status=JobStatus.applied))

    cycle = effective_cycle(job)
    assert cycle.resume_id is None
    assert cycle.resume_usage is ResumeUsage.unknown
    assert cycle.resume_attributed is False


def test_a_human_can_fill_in_a_historical_attribution(db):
    resume = make_resume(db, variant_name="Cloud版")
    job = legacy_application(db, make_job(db, status=JobStatus.applied))

    application_workflow.attribute_resume(
        db, job.id, AttributeResumeRequest(resume_id=resume.id)
    )

    db.refresh(job)
    cycle = effective_cycle(job)
    assert cycle.resume_id == resume.id
    assert cycle.resume_attribution_corrected is True


def test_the_correction_appends_rather_than_rewriting_history(db):
    resume = make_resume(db)
    job = legacy_application(db, make_job(db, status=JobStatus.applied))
    applied_event = next(e for e in job.events if e.event_type is EventType.applied)

    application_workflow.attribute_resume(
        db, job.id, AttributeResumeRequest(resume_id=resume.id)
    )

    db.refresh(applied_event)
    assert applied_event.resume_id is None, "the original applied row is untouched"

    db.refresh(job)
    kinds = [e.event_type for e in job.events]
    assert EventType.application_resume_attributed in kinds


def test_correcting_an_existing_attribution_is_a_different_event(db):
    first = make_resume(db, variant_name="Cloud版")
    second = make_resume(db, variant_name="DevOps版")
    job = make_job(db, status=JobStatus.new)
    apply_with(db, job, first)

    application_workflow.attribute_resume(
        db, job.id, AttributeResumeRequest(resume_id=second.id)
    )

    db.refresh(job)
    kinds = [e.event_type for e in job.events]
    assert EventType.application_resume_changed in kinds
    assert EventType.application_resume_attributed not in kinds
    assert effective_cycle(job).resume_id == second.id


def test_a_correction_records_what_it_replaced(db):
    first = make_resume(db, variant_name="Cloud版")
    second = make_resume(db, variant_name="DevOps版")
    job = make_job(db, status=JobStatus.new)
    apply_with(db, job, first)

    outcome = application_workflow.attribute_resume(
        db, job.id, AttributeResumeRequest(resume_id=second.id)
    )

    assert outcome.event.metadata_json["previous_resume_id"] == first.id
    assert outcome.event.resume_id == second.id


def test_a_correction_targets_one_specific_cycle(db):
    resume_a = make_resume(db, variant_name="Cloud版")
    resume_b = make_resume(db, variant_name="DevOps版")
    job = make_job(db, status=JobStatus.new)

    legacy_application(db, job, days_ago=40)
    first_applied = job.events[-1].id if job.events else None
    application_workflow.reset_status(db, job.id, ResetRequest())
    apply_with(db, job, resume_b)
    db.refresh(job)

    first_applied = build_cycles(job)[0].applied_event_id
    application_workflow.attribute_resume(
        db,
        job.id,
        AttributeResumeRequest(applied_event_id=first_applied, resume_id=resume_a.id),
    )
    db.refresh(job)

    cycles = build_cycles(job)
    assert cycles[0].resume_id == resume_a.id, "the old, superseded cycle was corrected"
    assert cycles[1].resume_id == resume_b.id, "the current cycle is untouched"


def test_an_archived_resume_may_be_used_for_a_historical_correction(db):
    """Past applications legitimately used variants since retired."""
    resume = make_resume(db, variant_name="旧版")
    resume_variants.archive(db, resume.id)
    job = legacy_application(db, make_job(db, status=JobStatus.applied))

    application_workflow.attribute_resume(
        db, job.id, AttributeResumeRequest(resume_id=resume.id)
    )

    db.refresh(job)
    assert effective_cycle(job).resume_id == resume.id


def test_attributing_a_job_with_no_application_is_rejected(db):
    resume = make_resume(db)
    job = make_job(db, status=JobStatus.new)
    with pytest.raises(ValidationError):
        application_workflow.attribute_resume(
            db, job.id, AttributeResumeRequest(resume_id=resume.id)
        )


def test_the_backfill_worklist_lists_only_unattributed_applications(db):
    resume = make_resume(db)
    attributed = make_job(db, status=JobStatus.new, company="有记录公司")
    apply_with(db, attributed, resume)

    missing = make_job(db, status=JobStatus.applied, company="缺记录公司")
    legacy_application(db, missing)

    never_applied = make_job(db, status=JobStatus.new)

    from app.services.application_analytics import load_jobs_for_analytics

    rows = resume_variants.unattributed_applications(db, load_jobs_for_analytics(db))
    assert [r["company"] for r in rows] == ["缺记录公司"]
    assert never_applied.id not in {r["job_id"] for r in rows}


def test_the_backfill_worklist_never_suggests_an_answer(db):
    make_resume(db, variant_name="Cloud版", active=True)
    job = legacy_application(db, make_job(db, status=JobStatus.applied))

    from app.services.application_analytics import load_jobs_for_analytics

    row = resume_variants.unattributed_applications(db, load_jobs_for_analytics(db))[0]
    assert "resume_id" not in row, "no guess is offered, not even the active resume"
    assert set(row) == {
        "job_id",
        "applied_event_id",
        "company",
        "title",
        "city",
        "applied_at",
    }
