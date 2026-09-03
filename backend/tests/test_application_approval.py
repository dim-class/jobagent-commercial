"""M6 — the per-job human confirmation gate.

Every test here is one of the ten items CLAUDE.md's M6 Implementation gate
requires before an implementation may be accepted. Offline only: no browser,
no network, no recruitment site, no model.
"""

from __future__ import annotations

import hashlib

import pytest

from app.core.errors import ValidationError
from app.models import ApplicationApproval, EventType, Job, JobStatus
from app.services import application_approval
from tests.conftest import make_job_payload

URL = "https://www.zhipin.com/job_detail/m6job1.html"


@pytest.fixture
def job(db) -> Job:
    payload = make_job_payload(company="M6 公司")
    row = Job(
        source="boss",
        external_id="m6job1",
        source_url=URL,
        company=payload["company"],
        title=payload["title"],
        city="北京",
        salary_text="20-30K",
        raw_description=payload["raw_description"],
        normalized_description=payload["raw_description"],
        content_hash=hashlib.sha256(b"m6job1").hexdigest(),
        status=JobStatus.reviewed,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def approve(db, job, active_resume):
    return application_approval.request_approval(
        db,
        job.id,
        resume_id=active_resume.id,
        answers="",
        answers_source=application_approval.ANSWERS_SOURCE_BOSS_DYNAMIC,
        confirmed=True,
    )


def observed():
    return {"observed_url": URL, "observed_external_id": "m6job1"}


def claim(db, approval):
    return application_approval.begin_attempt(db, approval.id, **observed())


# --------------------------------------------------------------------------
# 1. with no explicit confirmation, execution never happens
# --------------------------------------------------------------------------


def test_an_unconfirmed_request_creates_no_approval(db, job, active_resume):
    with pytest.raises(ValidationError):
        application_approval.request_approval(
            db,
            job.id,
            resume_id=active_resume.id,
            answers="",
            answers_source=application_approval.ANSWERS_SOURCE_BOSS_DYNAMIC,
            confirmed=False,
        )
    assert db.query(ApplicationApproval).count() == 0


def test_a_job_with_no_approval_has_nothing_to_execute(db, job):
    assert application_approval.latest_pending(db, job.id) is None


def test_dynamic_greeting_must_have_empty_text_and_the_fixed_source(db, job, active_resume):
    for answers, source in (
        ("猜测文本", application_approval.ANSWERS_SOURCE_BOSS_DYNAMIC),
        ("   ", application_approval.ANSWERS_SOURCE_BOSS_DYNAMIC),
        ("", "human_attested_unverified"),
        ("文本", "ai_generated"),
        ("文本", "site_verified"),
    ):
        with pytest.raises(ValidationError):
            application_approval.request_approval(
                db,
                job.id,
                resume_id=active_resume.id,
                answers=answers,
                answers_source=source,
                confirmed=True,
            )
    assert db.query(ApplicationApproval).count() == 0


def test_a_typed_greeting_is_bound_verbatim_and_cannot_be_empty(db, job, active_resume):
    """The mode authorized 2026-09-03: JobAgent types this exact text.

    This is not the mode it replaced. That one asked the human to *predict*
    what BOSS would send, and a live run disproved the prediction. This text is
    what JobAgent itself types into an empty box, so binding it verbatim is
    meaningful: change one character and the approval goes stale rather than
    sending something the human did not read.
    """
    greeting = "您好，我有近2年云基础设施经验，希望进一步沟通。"
    approval = application_approval.request_approval(
        db,
        job.id,
        resume_id=active_resume.id,
        answers=greeting,
        answers_source=application_approval.ANSWERS_SOURCE_TYPED,
        confirmed=True,
    )
    assert approval.answers_text == greeting
    assert approval.answers_hash == application_approval._answers_hash(greeting)

    # Editing the bound text invalidates it - the same rule the resume follows.
    approval.answers_text = greeting + "！"
    db.commit()
    assert application_approval.validate(db, approval).ok is False


def test_a_typed_greeting_refuses_an_empty_or_oversized_body(db, job, active_resume):
    for answers in ("", "   ", "字" * 1001):
        with pytest.raises(ValidationError):
            application_approval.request_approval(
                db,
                job.id,
                resume_id=active_resume.id,
                answers=answers,
                answers_source=application_approval.ANSWERS_SOURCE_TYPED,
                confirmed=True,
            )
    assert db.query(ApplicationApproval).count() == 0


def test_dynamic_greeting_approval_persists_no_message_body(db, job, active_resume):
    approval = approve(db, job, active_resume)
    assert approval.answers_text == ""
    assert approval.answers_hash == application_approval._answers_hash("")
    assert approval.answers_source == application_approval.ANSWERS_SOURCE_BOSS_DYNAMIC


# --------------------------------------------------------------------------
# 2. an approval binds exactly one job and cannot be reused for another
# --------------------------------------------------------------------------


def test_an_approval_names_one_job_and_snapshots_what_was_shown(db, job, active_resume):
    approval = approve(db, job, active_resume)
    assert approval.job_id == job.id
    assert approval.company == job.company
    assert approval.title == job.title
    assert approval.canonical_url == URL
    assert approval.external_id == "m6job1"
    assert approval.resume_id == active_resume.id
    assert approval.state == "pending"


def test_an_approval_cannot_be_executed_against_a_different_job(db, job, active_resume):
    approval = approve(db, job, active_resume)
    other = application_approval.validate(
        db,
        approval,
        observed_url="https://www.zhipin.com/job_detail/someone-else.html",
        observed_external_id="someone-else",
    )
    assert other.ok is False
    assert other.reason == application_approval.INVALID_IDENTITY_MISMATCH


def test_a_second_confirmation_supersedes_the_first(db, job, active_resume):
    """Two approvals for one job must never both be executable."""
    first = approve(db, job, active_resume)
    second = approve(db, job, active_resume)

    db.refresh(first)
    assert first.state == "invalidated"
    assert first.invalidated_reason == "superseded"
    assert second.state == "pending"
    assert application_approval.validate(db, first).reason == application_approval.INVALID_STATE


def test_begin_atomically_makes_the_one_attempt_non_reusable(db, job, active_resume):
    approval = approve(db, job, active_resume)
    claimed = claim(db, approval)
    assert claimed.state == "executing"
    assert claimed.attempt_started_at is not None
    assert application_approval.latest_pending(db, job.id) is None
    with pytest.raises(ValidationError):
        claim(db, approval)


def test_an_executing_attempt_blocks_a_second_approval(db, job, active_resume):
    """A new confirmation must not race an already-claimed browser action."""
    first = approve(db, job, active_resume)
    claim(db, first)

    with pytest.raises(ValidationError) as caught:
        approve(db, job, active_resume)

    assert caught.value.detail["reason"] == application_approval.INVALID_STATE
    assert db.query(ApplicationApproval).filter_by(job_id=job.id).count() == 1


def test_outcome_cannot_be_recorded_before_attempt_is_claimed(db, job, active_resume):
    approval = approve(db, job, active_resume)
    with pytest.raises(ValidationError):
        application_approval.record_outcome(
            db, approval.id, outcome=application_approval.OUTCOME_UNKNOWN, **observed()
        )
    db.refresh(approval)
    assert approval.state == "pending"


# --------------------------------------------------------------------------
# 3. a stale approval is refused before any browser action
# --------------------------------------------------------------------------


def test_a_changed_job_invalidates_the_approval(db, job, active_resume):
    approval = approve(db, job, active_resume)
    job.title = "换了一个职位名"
    db.commit()

    check = application_approval.validate(db, approval)
    assert check.ok is False
    assert check.reason == application_approval.INVALID_JOB_CHANGED


def test_a_relisted_url_invalidates_the_approval(db, job, active_resume):
    approval = approve(db, job, active_resume)
    job.source_url = "https://www.zhipin.com/job_detail/moved.html"
    db.commit()
    assert application_approval.validate(db, approval).reason == (
        application_approval.INVALID_JOB_CHANGED
    )


def test_recording_an_outcome_on_a_stale_approval_writes_nothing(db, job, active_resume):
    approval = approve(db, job, active_resume)
    job.company = "改名之后的公司"
    db.commit()

    with pytest.raises(ValidationError):
        application_approval.record_outcome(
            db, approval.id, outcome=application_approval.OUTCOME_APPLIED, **observed()
        )

    db.refresh(job)
    db.refresh(approval)
    assert job.status is JobStatus.reviewed, "a stale approval must never apply"
    assert approval.state == "pending"
    assert approval.outcome is None


# --------------------------------------------------------------------------
# 4. changing the resume variant or the answers invalidates the confirmation
# --------------------------------------------------------------------------


def test_editing_the_resume_content_invalidates_the_approval(db, job, active_resume):
    approval = approve(db, job, active_resume)
    active_resume.content_hash = "0" * 64
    db.commit()

    check = application_approval.validate(db, approval)
    assert check.ok is False
    assert check.reason == application_approval.INVALID_RESUME_CHANGED


def test_editing_the_answers_invalidates_the_approval(db, job, active_resume):
    approval = approve(db, job, active_resume)
    approval.answers_text = "偷偷改过的内容"  # what a tampered row would look like
    db.commit()

    check = application_approval.validate(db, approval)
    assert check.ok is False
    assert check.reason == application_approval.INVALID_ANSWERS_CHANGED


def test_a_legacy_expected_text_approval_is_stale(db, job, active_resume):
    approval = approve(db, job, active_resume)
    approval.answers_source = "human_attested_unverified"
    approval.answers_text = "旧版人工猜测正文"
    approval.answers_hash = application_approval._answers_hash(approval.answers_text)
    db.commit()

    check = application_approval.validate(db, approval)
    assert check.ok is False
    assert check.reason == application_approval.INVALID_ANSWERS_SOURCE


# --------------------------------------------------------------------------
# 5. a job-identity mismatch at submit time stops the attempt
# --------------------------------------------------------------------------


def test_identity_is_rechecked_at_submit_time(db, job, active_resume):
    approval = approve(db, job, active_resume)
    assert application_approval.validate(
        db, approval, observed_url=URL, observed_external_id="m6job1"
    ).ok
    claim(db, approval)

    with pytest.raises(ValidationError):
        application_approval.record_outcome(
            db,
            approval.id,
            outcome=application_approval.OUTCOME_APPLIED,
            observed_url=URL,
            observed_external_id="a-different-id",
        )
    db.refresh(job)
    assert job.status is JobStatus.reviewed


def test_a_query_string_on_the_observed_url_still_matches(db, job, active_resume):
    """BOSS appends lid/securityId; canonicalisation is not an identity change."""
    approval = approve(db, job, active_resume)
    check = application_approval.validate(
        db,
        approval,
        observed_url=URL + "?lid=SECRET&securityId=TOKEN",
        observed_external_id="m6job1",
    )
    assert check.ok is True


def test_recording_any_outcome_requires_both_observed_identity_fields(db, job, active_resume):
    approval = approve(db, job, active_resume)
    for supplied in (
        {},
        {"observed_url": URL},
        {"observed_external_id": "m6job1"},
    ):
        with pytest.raises(ValidationError):
            application_approval.record_outcome(
                db,
                approval.id,
                outcome=application_approval.OUTCOME_UNKNOWN,
                **supplied,
            )
        db.refresh(approval)
        assert approval.state == "pending"
        assert approval.outcome is None


# --------------------------------------------------------------------------
# 6. an uncertain result is recorded as application_result_unknown, never applied
# --------------------------------------------------------------------------


def test_an_unverifiable_result_never_becomes_applied(db, job, active_resume):
    approval = approve(db, job, active_resume)
    claim(db, approval)
    application_approval.record_outcome(
        db, approval.id, outcome=application_approval.OUTCOME_UNKNOWN,
        detail="页面未给出确认", **observed()
    )

    db.refresh(job)
    db.refresh(approval)
    assert job.status is JobStatus.reviewed, "Job.status must be left alone"
    assert approval.state == "consumed"
    assert approval.outcome == "unknown"
    assert approval.applied_event_id is None

    kinds = [event.event_type for event in job.events]
    assert EventType.application_result_unknown in kinds
    assert EventType.applied not in kinds


def test_a_failed_attempt_is_not_an_application(db, job, active_resume):
    approval = approve(db, job, active_resume)
    claim(db, approval)
    application_approval.record_outcome(
        db, approval.id, outcome=application_approval.OUTCOME_FAILED, **observed()
    )
    db.refresh(job)
    assert job.status is JobStatus.reviewed
    assert EventType.applied not in [event.event_type for event in job.events]


# --------------------------------------------------------------------------
# 7. one verified submission produces exactly one applied event
# --------------------------------------------------------------------------


def test_a_verified_submission_applies_exactly_once(db, job, active_resume):
    approval = approve(db, job, active_resume)
    claim(db, approval)
    application_approval.record_outcome(
        db,
        approval.id,
        outcome=application_approval.OUTCOME_APPLIED,
        observed_url=URL,
        observed_external_id="m6job1",
    )

    db.refresh(job)
    db.refresh(approval)
    assert job.status is JobStatus.applied
    assert approval.state == "consumed"
    assert approval.applied_event_id is not None

    applied = [e for e in job.events if e.event_type is EventType.applied]
    assert len(applied) == 1
    assert applied[0].resume_id == active_resume.id, "v0.7 attribution is preserved"


def test_a_consumed_approval_cannot_be_used_again(db, job, active_resume):
    approval = approve(db, job, active_resume)
    claim(db, approval)
    application_approval.record_outcome(
        db, approval.id, outcome=application_approval.OUTCOME_APPLIED, **observed()
    )

    with pytest.raises(ValidationError):
        application_approval.record_outcome(
            db, approval.id, outcome=application_approval.OUTCOME_APPLIED, **observed()
        )

    db.refresh(job)
    assert len([e for e in job.events if e.event_type is EventType.applied]) == 1


def test_an_already_applied_job_cannot_be_approved_again(db, job, active_resume):
    approval = approve(db, job, active_resume)
    claim(db, approval)
    application_approval.record_outcome(
        db, approval.id, outcome=application_approval.OUTCOME_APPLIED, **observed()
    )
    with pytest.raises(ValidationError):
        approve(db, job, active_resume)


# --------------------------------------------------------------------------
# 8. no bulk-apply path, and no AI-result -> execution path
# --------------------------------------------------------------------------


def test_the_service_exposes_no_bulk_or_filter_based_approval():
    """One call authorizes one job. A plural entry point is the bug."""
    import inspect

    for name, fn in vars(application_approval).items():
        if name.startswith("_") or not inspect.isfunction(fn):
            continue
        params = inspect.signature(fn).parameters
        assert "job_ids" not in params, f"{name} takes a list of jobs"
        assert "job_id" not in params or params["job_id"].annotation is not list


def test_no_code_path_leads_from_an_analysis_result_to_an_execution():
    """M6 section 1: an AI verdict or score may never initiate an application.

    Checked structurally (imports + attribute names), not by grepping prose -
    the module's own docstring says the words on purpose.
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path(application_approval.__file__).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)

    for forbidden in ("JobAnalysis", "verdict", "overall_score", "analyze_job"):
        assert forbidden not in names, f"{forbidden} must not reach the apply gate"
    assert not any("job_matcher" in name for name in names)
    assert not any("task_matching" in name for name in names)


def test_a_job_without_a_verifiable_identity_cannot_be_approved(db, active_resume):
    """No canonical URL / external id means nothing to re-check at submit time."""
    row = Job(
        source="manual",
        external_id=None,
        source_url=None,
        company="没有链接的公司",
        title="岗位",
        raw_description="JD",
        normalized_description="JD",
        content_hash=hashlib.sha256(b"m6-no-url").hexdigest(),
        status=JobStatus.reviewed,
    )
    db.add(row)
    db.commit()

    with pytest.raises(ValidationError):
        application_approval.request_approval(
            db,
            row.id,
            resume_id=active_resume.id,
            answers="x",
            answers_source=application_approval.ANSWERS_SOURCE_BOSS_DYNAMIC,
            confirmed=True,
        )


# --------------------------------------------------------------------------
# a crashed attempt must stay fail-closed AND stay resolvable by the human
# --------------------------------------------------------------------------


def test_a_crashed_attempt_can_be_resolved_by_the_human_and_unblocks_the_job(
    db, job, active_resume
):
    """If the worker dies between `begin` and `outcome`, the approval is stuck
    in `executing`: never auto-retried (correct) but also never resolvable,
    which permanently blocks the job from M6 with nothing on its trail.

    The human - who can look at BOSS themselves - closes it out. That records
    the honest answer (`unknown`), never touches `Job.status`, and lets a fresh
    confirmation be made.
    """
    approval = approve(db, job, active_resume)
    claim(db, approval)  # the browser action starts, then the worker dies

    with pytest.raises(ValidationError):
        approve(db, job, active_resume)

    resolved = application_approval.abandon_attempt(db, approval.id, confirmed=True)
    assert resolved.state == "consumed"
    assert resolved.outcome == application_approval.OUTCOME_UNKNOWN
    assert resolved.applied_event_id is None

    db.refresh(job)
    assert job.status is JobStatus.reviewed, "resolving is not applying"
    assert EventType.application_result_unknown in [e.event_type for e in job.events]

    fresh = approve(db, job, active_resume)
    assert fresh.state == "pending"


def test_resolving_a_crashed_attempt_needs_explicit_confirmation(db, job, active_resume):
    approval = approve(db, job, active_resume)
    claim(db, approval)
    with pytest.raises(ValidationError):
        application_approval.abandon_attempt(db, approval.id, confirmed=False)
    db.refresh(approval)
    assert approval.state == "executing"


def test_resolving_can_never_mark_a_job_applied(db, job, active_resume):
    """The human resolving a stuck attempt reports uncertainty, not success."""
    approval = approve(db, job, active_resume)
    claim(db, approval)
    application_approval.abandon_attempt(db, approval.id, confirmed=True)
    db.refresh(job)
    assert job.status is not JobStatus.applied
    assert EventType.applied not in [e.event_type for e in job.events]
