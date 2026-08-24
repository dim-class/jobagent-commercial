"""Interview process and round lifecycle (v0.8).

The load-bearing tests are about *attribution*: a process belongs to one
application cycle, and which resume gets credit follows from that cycle - never
from ``Job.status`` and never from whichever resume is active today.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.core.errors import NotFoundError, ValidationError
from app.models import (
    EventType,
    InterviewFailureReason,
    InterviewLocationType,
    InterviewOutcome,
    InterviewProcess,
    InterviewProcessStatus,
    InterviewRoundStatus,
    InterviewRoundType,
    JobStatus,
    Resume,
    WithdrawReason,
)
from app.schemas.application import (
    InterviewRequest,
    OfferRequest,
    RejectRequest,
    ResetRequest,
)
from app.schemas.interview import (
    PreparationNotes,
    ProcessCreateRequest,
    ProcessWithdrawRequest,
    RoundCancelRequest,
    RoundCompleteRequest,
    RoundCreateRequest,
    RoundUpdateRequest,
)
from app.services import application_workflow, interview_pipeline, resume_variants
from app.services.application_cycles import build_cycles, effective_cycle

from tests.test_career_analytics import NOW, make_job
from tests.test_resume_variants import apply_with, make_resume

FUTURE = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------


def applied_job(db, resume=None, **job_kwargs):
    job = make_job(db, status=JobStatus.new, **job_kwargs)
    apply_with(db, job, resume)
    db.refresh(job)
    return job


def open_process(db, job) -> InterviewProcess:
    return interview_pipeline.create_process(db, job.id, ProcessCreateRequest())


def add(db, process, round_type=InterviewRoundType.hr, **kwargs):
    return interview_pipeline.add_round(
        db, process.id, RoundCreateRequest(round_type=round_type, **kwargs)
    )


def complete(db, round_id, outcome=InterviewOutcome.passed, **kwargs):
    return interview_pipeline.complete_round(
        db, round_id, RoundCompleteRequest(confirmed=True, outcome=outcome, **kwargs)
    )


# --------------------------------------------------------------------------
# process creation
# --------------------------------------------------------------------------


def test_a_process_belongs_to_the_application_cycle(db):
    resume = make_resume(db, variant_name="DevOps版")
    job = applied_job(db, resume)

    process = open_process(db, job)

    cycle = effective_cycle(job)
    assert process.applied_event_id == cycle.applied_event_id
    assert process.job_id == job.id
    assert process.status is InterviewProcessStatus.ongoing


def test_a_job_with_no_application_cannot_have_a_process(db):
    job = make_job(db, status=JobStatus.new)
    with pytest.raises(ValidationError) as excinfo:
        open_process(db, job)
    assert "还没有投递记录" in str(excinfo.value)


def test_one_cycle_gets_only_one_process(db):
    job = applied_job(db)
    open_process(db, job)

    with pytest.raises(ValidationError) as excinfo:
        open_process(db, job)
    assert "已经有面试流程" in str(excinfo.value)


def test_a_withdrawn_application_cannot_open_a_process(db):
    job = applied_job(db)
    application_workflow.reset_status(db, job.id, ResetRequest())
    db.refresh(job)

    with pytest.raises(ValidationError):
        open_process(db, job)


def test_a_process_can_target_a_specific_older_cycle(db):
    cloud = make_resume(db, variant_name="Cloud版")
    devops = make_resume(db, variant_name="DevOps版")
    job = applied_job(db, cloud)
    application_workflow.reset_status(db, job.id, ResetRequest())
    apply_with(db, job, devops)
    db.refresh(job)

    first_cycle = build_cycles(job)[0]
    process = interview_pipeline.create_process(
        db, job.id, ProcessCreateRequest(applied_event_id=first_cycle.applied_event_id)
    )
    assert process.applied_event_id == first_cycle.applied_event_id


def test_an_unknown_cycle_is_a_clean_404(db):
    job = applied_job(db)
    with pytest.raises(NotFoundError):
        interview_pipeline.create_process(
            db, job.id, ProcessCreateRequest(applied_event_id=999999)
        )


def test_a_process_can_open_with_a_first_round(db):
    job = applied_job(db)
    process = interview_pipeline.create_process(
        db,
        job.id,
        ProcessCreateRequest(first_round=RoundCreateRequest(round_type=InterviewRoundType.hr)),
    )
    assert len(process.rounds) == 1
    assert process.rounds[0].round_type is InterviewRoundType.hr


# --------------------------------------------------------------------------
# rounds
# --------------------------------------------------------------------------


def test_rounds_are_indexed_in_the_order_they_are_added(db):
    job = applied_job(db)
    process = open_process(db, job)

    add(db, process, InterviewRoundType.hr)
    add(db, process, InterviewRoundType.technical)
    process, _ = add(db, process, InterviewRoundType.final)

    assert [(r.round_index, r.round_type) for r in process.rounds] == [
        (1, InterviewRoundType.hr),
        (2, InterviewRoundType.technical),
        (3, InterviewRoundType.final),
    ]


def test_a_round_index_can_be_corrected(db):
    """HR added after technical by mistake - a numeric fix, not drag-and-drop."""
    job = applied_job(db)
    process = open_process(db, job)
    _, technical = add(db, process, InterviewRoundType.technical)
    _, hr = add(db, process, InterviewRoundType.hr)

    interview_pipeline.update_round(db, hr.id, RoundUpdateRequest(round_index=1))
    interview_pipeline.update_round(db, technical.id, RoundUpdateRequest(round_index=2))

    process = interview_pipeline.get_process(db, process.id)
    assert [r.round_type for r in process.rounds] == [
        InterviewRoundType.hr,
        InterviewRoundType.technical,
    ]


def test_a_round_with_a_time_is_scheduled_and_one_without_is_planned(db):
    job = applied_job(db)
    process = open_process(db, job)

    _, planned = add(db, process, InterviewRoundType.hr)
    _, scheduled = add(db, process, InterviewRoundType.technical, scheduled_at=FUTURE)

    assert planned.status is InterviewRoundStatus.planned
    assert scheduled.status is InterviewRoundStatus.scheduled
    # SQLite stores no timezone, so the value comes back naive.
    assert scheduled.scheduled_at.replace(tzinfo=timezone.utc) == FUTURE


def test_adding_a_round_moves_the_job_to_interview(db):
    job = applied_job(db)
    process = open_process(db, job)
    add(db, process, InterviewRoundType.hr)

    db.refresh(job)
    assert job.status is JobStatus.interview


def test_adding_a_round_appends_one_timeline_event(db):
    job = applied_job(db)
    process = open_process(db, job)
    add(db, process, InterviewRoundType.hr, scheduled_at=FUTURE)

    db.refresh(job)
    kinds = [e.event_type for e in job.events]
    assert kinds.count(EventType.interview_scheduled) == 1


def test_editing_a_round_emits_no_event(db):
    """The timeline is a milestone log, not a field-change log."""
    job = applied_job(db)
    process = open_process(db, job)
    _, row = add(db, process, InterviewRoundType.hr)
    db.refresh(job)
    before = len(job.events)

    interview_pipeline.update_round(
        db, row.id, RoundUpdateRequest(interviewer_name="王女士", duration_minutes=45)
    )

    db.refresh(job)
    assert len(job.events) == before


def test_round_details_round_trip(db):
    job = applied_job(db)
    process = open_process(db, job)
    _, row = add(
        db,
        process,
        InterviewRoundType.technical,
        scheduled_at=FUTURE,
        duration_minutes=60,
        location_type=InterviewLocationType.online,
        meeting_url="https://meet.example.com/abc?token=secret",
        interviewer_name="李工",
        interviewer_role="技术负责人",
    )

    assert row.duration_minutes == 60
    assert row.location_type is InterviewLocationType.online
    assert row.interviewer_role == "技术负责人"
    assert row.meeting_url.endswith("token=secret")


def test_a_custom_name_overrides_the_type_label(db):
    job = applied_job(db)
    process = open_process(db, job)
    _, row = add(db, process, InterviewRoundType.technical, custom_round_name="技术二面")
    assert row.display_name == "技术二面"


def test_clearing_a_scheduled_time_returns_the_round_to_planned(db):
    job = applied_job(db)
    process = open_process(db, job)
    _, row = add(db, process, InterviewRoundType.hr, scheduled_at=FUTURE)

    _, row = interview_pipeline.update_round(
        db, row.id, RoundUpdateRequest(clear_scheduled_at=True)
    )
    assert row.scheduled_at is None
    assert row.status is InterviewRoundStatus.planned


# --------------------------------------------------------------------------
# completing rounds
# --------------------------------------------------------------------------


def test_completing_a_round_requires_confirmation(db):
    job = applied_job(db)
    process = open_process(db, job)
    _, row = add(db, process, InterviewRoundType.hr)

    with pytest.raises(ValidationError):
        interview_pipeline.complete_round(
            db, row.id, RoundCompleteRequest(outcome=InterviewOutcome.passed)
        )


def test_a_passed_round_keeps_the_process_ongoing(db):
    job = applied_job(db)
    process = open_process(db, job)
    _, row = add(db, process, InterviewRoundType.hr)

    process, row, _ = complete(db, row.id, InterviewOutcome.passed)

    assert row.outcome is InterviewOutcome.passed
    assert row.status is InterviewRoundStatus.completed
    assert row.completed_at is not None
    assert process.status is InterviewProcessStatus.ongoing


def test_a_pending_outcome_is_not_a_failure(db):
    job = applied_job(db)
    process = open_process(db, job)
    _, row = add(db, process, InterviewRoundType.technical)

    _, row, _ = complete(db, row.id, InterviewOutcome.pending)

    assert row.status is InterviewRoundStatus.completed
    assert row.outcome is InterviewOutcome.pending


def test_a_failed_round_alone_does_not_reject_the_job(db):
    """Failing a round is not the same as the employer closing the door."""
    job = applied_job(db)
    process = open_process(db, job)
    _, row = add(db, process, InterviewRoundType.technical)

    process, _, job_status = complete(db, row.id, InterviewOutcome.failed)

    db.refresh(job)
    assert job_status is None
    assert job.status is JobStatus.interview
    assert process.status is InterviewProcessStatus.ongoing


def test_a_failed_round_can_explicitly_record_the_rejection(db):
    job = applied_job(db)
    process = open_process(db, job)
    _, row = add(db, process, InterviewRoundType.technical)

    process, _, job_status = complete(
        db,
        row.id,
        InterviewOutcome.failed,
        failure_reason=InterviewFailureReason.technical_depth,
        also_record_rejection=True,
    )

    db.refresh(job)
    assert job_status == "rejected"
    assert job.status is JobStatus.rejected
    assert process.status is InterviewProcessStatus.rejected
    assert process.ended_after_round_type is InterviewRoundType.technical
    assert process.failure_reason is InterviewFailureReason.technical_depth


def test_recording_a_rejection_needs_a_failed_outcome(db):
    from pydantic import ValidationError as PydanticError

    with pytest.raises(PydanticError):
        RoundCompleteRequest(
            confirmed=True, outcome=InterviewOutcome.passed, also_record_rejection=True
        )


def test_a_failure_reason_only_applies_to_a_failed_round(db):
    from pydantic import ValidationError as PydanticError

    with pytest.raises(PydanticError):
        RoundCompleteRequest(
            confirmed=True,
            outcome=InterviewOutcome.passed,
            failure_reason=InterviewFailureReason.salary,
        )


def test_a_rejection_does_not_erase_previously_passed_rounds(db):
    """Four cleared rounds are still four cleared rounds."""
    job = applied_job(db)
    process = open_process(db, job)
    _, hr = add(db, process, InterviewRoundType.hr)
    _, tech1 = add(db, process, InterviewRoundType.technical)
    _, tech2 = add(db, process, InterviewRoundType.technical)

    complete(db, hr.id, InterviewOutcome.passed)
    complete(db, tech1.id, InterviewOutcome.passed)
    process, _, _ = complete(
        db, tech2.id, InterviewOutcome.failed, also_record_rejection=True
    )

    passed = [r for r in process.rounds if r.outcome is InterviewOutcome.passed]
    assert len(passed) == 2
    assert interview_pipeline.rounds_passed(process) == 2
    assert process.status is InterviewProcessStatus.rejected


def test_a_completed_round_cannot_be_silently_re_recorded(db):
    job = applied_job(db)
    process = open_process(db, job)
    _, row = add(db, process, InterviewRoundType.hr)
    complete(db, row.id, InterviewOutcome.passed)

    with pytest.raises(ValidationError) as excinfo:
        complete(db, row.id, InterviewOutcome.failed)
    assert "已经记录过结果" in str(excinfo.value)


def test_an_explicit_correction_is_allowed_and_audited(db):
    job = applied_job(db)
    process = open_process(db, job)
    _, row = add(db, process, InterviewRoundType.hr)
    complete(db, row.id, InterviewOutcome.passed)

    _, row, _ = complete(db, row.id, InterviewOutcome.failed, correction=True)

    assert row.outcome is InterviewOutcome.failed
    db.refresh(job)
    corrections = [
        e for e in job.events if e.event_type is EventType.interview_round_corrected
    ]
    assert len(corrections) == 1
    assert corrections[0].metadata_json["previous_outcome"] == "passed"
    assert EventType.interview_passed in {e.event_type for e in job.events}, (
        "the original result event stays in the trail"
    )


def test_feedback_and_notes_are_stored_separately(db):
    """What the interviewer said vs. what the user remembers."""
    job = applied_job(db)
    process = open_process(db, job)
    _, row = add(db, process, InterviewRoundType.technical)

    _, row, _ = complete(
        db,
        row.id,
        InterviewOutcome.failed,
        feedback_text="面试官说 Kubernetes 深度不够",
        notes="我自己觉得网络那题答得不好",
        failure_reason=InterviewFailureReason.technical_depth,
    )

    assert row.feedback_text == "面试官说 Kubernetes 深度不够"
    assert row.notes == "我自己觉得网络那题答得不好"
    assert row.feedback_text != row.notes


def test_feedback_tags_are_stored_as_chosen(db):
    from app.models import FeedbackTag

    job = applied_job(db)
    process = open_process(db, job)
    _, row = add(db, process, InterviewRoundType.technical)

    _, row, _ = complete(
        db,
        row.id,
        InterviewOutcome.failed,
        feedback_tags=[FeedbackTag.technical_depth, FeedbackTag.cloud],
    )
    assert set(row.feedback_tags) == {"technical_depth", "cloud"}


def test_preparation_notes_round_trip(db):
    job = applied_job(db)
    process = open_process(db, job)
    _, row = add(db, process, InterviewRoundType.technical)

    _, row = interview_pipeline.update_round(
        db,
        row.id,
        RoundUpdateRequest(
            preparation=PreparationNotes(
                questions_asked=["讲讲 K8s 调度"],
                weak_points=["etcd 细节"],
                follow_up_topics=["复习 CNI"],
            )
        ),
    )
    assert row.preparation_json["questions_asked"] == ["讲讲 K8s 调度"]
    assert row.preparation_json["weak_points"] == ["etcd 细节"]


# --------------------------------------------------------------------------
# cancel / withdraw / offer
# --------------------------------------------------------------------------


def test_cancelling_a_round(db):
    job = applied_job(db)
    process = open_process(db, job)
    _, row = add(db, process, InterviewRoundType.hr, scheduled_at=FUTURE)

    _, row = interview_pipeline.cancel_round(
        db, row.id, RoundCancelRequest(reason="对方临时改期")
    )

    assert row.status is InterviewRoundStatus.cancelled
    assert row.outcome is InterviewOutcome.unknown
    assert "对方临时改期" in row.notes


def test_a_completed_round_cannot_be_cancelled(db):
    job = applied_job(db)
    process = open_process(db, job)
    _, row = add(db, process, InterviewRoundType.hr)
    complete(db, row.id)

    with pytest.raises(ValidationError):
        interview_pipeline.cancel_round(db, row.id, RoundCancelRequest())


def test_withdrawing_is_not_a_rejection(db):
    job = applied_job(db)
    process = open_process(db, job)
    _, row = add(db, process, InterviewRoundType.technical)
    complete(db, row.id, InterviewOutcome.passed)

    process = interview_pipeline.withdraw_process(
        db,
        process.id,
        ProcessWithdrawRequest(
            confirmed=True, reason=WithdrawReason.accepted_other_offer
        ),
    )

    assert process.status is InterviewProcessStatus.withdrawn
    assert process.status is not InterviewProcessStatus.rejected
    assert process.withdraw_reason is WithdrawReason.accepted_other_offer
    assert process.ended_after_round_type is InterviewRoundType.technical

    db.refresh(job)
    assert job.status is not JobStatus.rejected, "withdrawing is not being rejected"
    assert EventType.interview_withdrawn in {e.event_type for e in job.events}


def test_withdrawing_requires_confirmation(db):
    job = applied_job(db)
    process = open_process(db, job)
    with pytest.raises(ValidationError):
        interview_pipeline.withdraw_process(db, process.id, ProcessWithdrawRequest())


def test_a_closed_process_cannot_be_withdrawn_again(db):
    job = applied_job(db)
    process = open_process(db, job)
    interview_pipeline.withdraw_process(
        db, process.id, ProcessWithdrawRequest(confirmed=True)
    )
    with pytest.raises(ValidationError):
        interview_pipeline.withdraw_process(
            db, process.id, ProcessWithdrawRequest(confirmed=True)
        )


def test_a_closed_process_cannot_gain_rounds(db):
    job = applied_job(db)
    process = open_process(db, job)
    interview_pipeline.withdraw_process(
        db, process.id, ProcessWithdrawRequest(confirmed=True)
    )
    with pytest.raises(ValidationError):
        add(db, process, InterviewRoundType.hr)


def test_recording_an_offer_closes_the_process_through_the_existing_path(db):
    job = applied_job(db)
    process = open_process(db, job)
    _, row = add(db, process, InterviewRoundType.final)
    complete(db, row.id, InterviewOutcome.passed)

    application_workflow.record_offer(db, job.id, OfferRequest(salary_text="35k"))

    db.refresh(job)
    process = interview_pipeline.get_process(db, process.id)
    assert job.status is JobStatus.offer
    assert process.status is InterviewProcessStatus.offer
    assert process.ended_after_round_type is InterviewRoundType.final
    assert EventType.offer in {e.event_type for e in job.events}, (
        "the workflow milestone still owns the offer event"
    )


def test_recording_a_rejection_closes_the_process(db):
    job = applied_job(db)
    process = open_process(db, job)
    _, row = add(db, process, InterviewRoundType.hr)
    complete(db, row.id, InterviewOutcome.passed)

    application_workflow.record_rejection(db, job.id, RejectRequest(reason="岗位取消"))

    process = interview_pipeline.get_process(db, process.id)
    assert process.status is InterviewProcessStatus.rejected
    assert process.ended_after_round_type is InterviewRoundType.hr


def test_a_cancelled_round_is_not_where_the_process_ended(db):
    job = applied_job(db)
    process = open_process(db, job)
    _, hr = add(db, process, InterviewRoundType.hr)
    complete(db, hr.id, InterviewOutcome.passed)
    _, tech = add(db, process, InterviewRoundType.technical)
    interview_pipeline.cancel_round(db, tech.id, RoundCancelRequest())

    application_workflow.record_rejection(db, job.id, RejectRequest())

    process = interview_pipeline.get_process(db, process.id)
    assert process.ended_after_round_type is InterviewRoundType.hr


# --------------------------------------------------------------------------
# attribution
# --------------------------------------------------------------------------


def test_the_process_carries_the_cycles_resume(db):
    resume = make_resume(db, variant_name="DevOps版")
    job = applied_job(db, resume)
    process = open_process(db, job)

    context = interview_pipeline.cycle_context(db, process)
    assert context.resume_id == resume.id
    assert context.resume_label == "DevOps版"


def test_reset_then_reapply_attributes_the_interview_to_cycle_two(db):
    """Cycle 1 with Cloud版, withdrawn. Cycle 2 with DevOps版 gets the interview."""
    cloud = make_resume(db, variant_name="Cloud版")
    devops = make_resume(db, variant_name="DevOps版")
    job = applied_job(db, cloud)
    application_workflow.reset_status(db, job.id, ResetRequest())
    apply_with(db, job, devops)
    db.refresh(job)

    process = open_process(db, job)
    add(db, process, InterviewRoundType.technical)

    context = interview_pipeline.cycle_context(db, process)
    assert context.resume_id == devops.id
    assert context.resume_id != cloud.id

    cycles = build_cycles(job)
    assert process.applied_event_id == cycles[1].applied_event_id


def test_switching_the_active_resume_does_not_move_the_interview(db):
    cloud = make_resume(db, variant_name="Cloud版", active=True)
    job = applied_job(db, cloud)
    process = open_process(db, job)
    add(db, process, InterviewRoundType.final)

    devops = make_resume(db, variant_name="DevOps版")
    cloud.is_active = False
    devops.is_active = True
    db.commit()

    context = interview_pipeline.cycle_context(db, process)
    assert context.resume_id == cloud.id, "attribution is frozen at application time"


def test_an_archived_resume_stays_visible_on_its_history(db):
    resume = make_resume(db, variant_name="旧版")
    job = applied_job(db, resume)
    process = open_process(db, job)
    add(db, process, InterviewRoundType.hr)

    resume_variants.archive(db, resume.id)

    context = interview_pipeline.cycle_context(db, process)
    assert context.resume_id == resume.id
    assert context.resume_label == "旧版"
    assert context.resume_archived is True


def test_an_unattributed_application_has_no_resume_on_its_process(db):
    make_resume(db, variant_name="Cloud版", active=True)
    job = applied_job(db, None)
    process = open_process(db, job)

    context = interview_pipeline.cycle_context(db, process)
    assert context.resume_id is None, "never the active resume"


def test_process_for_job_returns_only_the_current_cycles_process(db):
    cloud = make_resume(db, variant_name="Cloud版")
    devops = make_resume(db, variant_name="DevOps版")
    job = applied_job(db, cloud)
    first_cycle = build_cycles(job)[0]
    old = interview_pipeline.create_process(
        db, job.id, ProcessCreateRequest(applied_event_id=first_cycle.applied_event_id)
    )
    application_workflow.reset_status(db, job.id, ResetRequest())
    apply_with(db, job, devops)
    db.refresh(job)

    assert interview_pipeline.process_for_job(db, job.id) is None, (
        "the superseded cycle's process is history, not the current state"
    )
    current = open_process(db, job)
    assert interview_pipeline.process_for_job(db, job.id).id == current.id
    assert current.id != old.id


# --------------------------------------------------------------------------
# the v0.4 记录面试 path
# --------------------------------------------------------------------------


def test_the_legacy_action_now_creates_a_round(db):
    """There is no parallel interview path any more."""
    from app.schemas.application import InterviewRound as LegacyRound

    job = applied_job(db)
    application_workflow.record_interview(
        db, job.id, InterviewRequest(round=LegacyRound.technical)
    )

    process = interview_pipeline.process_for_job(db, job.id)
    assert process is not None
    assert len(process.rounds) == 1
    assert process.rounds[0].round_type is InterviewRoundType.technical
    db.refresh(job)
    assert job.status is JobStatus.interview


def test_the_legacy_action_reuses_an_existing_process(db):
    from app.schemas.application import InterviewRound as LegacyRound

    job = applied_job(db)
    application_workflow.record_interview(db, job.id, InterviewRequest(round=LegacyRound.hr))
    application_workflow.record_interview(
        db, job.id, InterviewRequest(round=LegacyRound.final)
    )

    processes = interview_pipeline.list_processes(db, job_id=job.id)
    assert len(processes) == 1
    assert len(processes[0].rounds) == 2


def test_the_legacy_action_keeps_its_old_metadata_key(db):
    from app.schemas.application import InterviewRound as LegacyRound

    job = applied_job(db)
    outcome = application_workflow.record_interview(
        db, job.id, InterviewRequest(round=LegacyRound.hr)
    )
    assert outcome.event.metadata_json["interview_round"] == "HR"


# --------------------------------------------------------------------------
# legacy events
# --------------------------------------------------------------------------


def test_a_pre_v08_interview_event_is_not_classified(db):
    """A generic old event says an interview happened - not which kind."""
    from tests.test_career_analytics import add_event

    job = applied_job(db)
    add_event(db, job, EventType.interview, at=NOW - timedelta(days=10))
    db.refresh(job)

    legacy = interview_pipeline.legacy_interview_events(db)
    assert [e.job_id for e in legacy] == [job.id]
    assert interview_pipeline.process_for_job(db, job.id) is None


def test_a_job_with_a_real_process_reports_no_legacy_event(db):
    job = applied_job(db)
    process = open_process(db, job)
    add(db, process, InterviewRoundType.hr)

    assert interview_pipeline.legacy_interview_events(db) == []


# --------------------------------------------------------------------------
# derived state
# --------------------------------------------------------------------------


def test_the_current_round_is_the_first_unfinished_one(db):
    job = applied_job(db)
    process = open_process(db, job)
    _, hr = add(db, process, InterviewRoundType.hr)
    add(db, process, InterviewRoundType.technical)
    complete(db, hr.id, InterviewOutcome.passed)

    process = interview_pipeline.get_process(db, process.id)
    current = interview_pipeline.current_round(process)
    assert current.round_type is InterviewRoundType.technical


def test_there_is_no_current_round_once_everything_is_done(db):
    job = applied_job(db)
    process = open_process(db, job)
    _, hr = add(db, process, InterviewRoundType.hr)
    complete(db, hr.id)

    process = interview_pipeline.get_process(db, process.id)
    assert interview_pipeline.current_round(process) is None


def test_next_scheduled_at_is_the_soonest_scheduled_round(db):
    job = applied_job(db)
    process = open_process(db, job)
    add(db, process, InterviewRoundType.technical, scheduled_at=FUTURE + timedelta(days=3))
    process, _ = add(db, process, InterviewRoundType.hr, scheduled_at=FUTURE)

    assert interview_pipeline.next_scheduled_at(process) == FUTURE


# --------------------------------------------------------------------------
# deleting a job (v1.0 regression)
# --------------------------------------------------------------------------


def test_deleting_a_job_with_an_interview_process_works(db):
    """v1.0: ``applied_event_id`` was RESTRICT and made such a job undeletable.

    The RESTRICT never protected the attribution it claimed to: the ``applied``
    event and the process are both children of the same job, so the ORM removed
    the events first and the constraint simply fired. v0.9 hit the identical
    problem on offers.
    """
    from app.models import InterviewProcess as IP
    from app.models import InterviewRound, Job

    job = applied_job(db)
    process = open_process(db, job)
    _, first = add(db, process, InterviewRoundType.hr)
    complete(db, first.id)
    add(db, process, InterviewRoundType.technical)

    process_id = process.id

    db.delete(db.get(Job, job.id))
    db.commit()

    assert db.get(IP, process_id) is None
    assert db.query(InterviewRound).filter_by(interview_process_id=process_id).count() == 0

    violations = db.execute(text("PRAGMA foreign_key_check")).fetchall()
    assert violations == []


def test_deleting_a_job_with_an_interview_over_http_works(client, db):
    job = applied_job(db)
    process = open_process(db, job)
    _, first = add(db, process, InterviewRoundType.hr)
    complete(db, first.id)

    response = client.delete(f"/api/jobs/{job.id}")
    assert response.status_code == 200, response.text
