"""Recording applications the human already made, from their own pasted list.

Authorized 2026-09-02, and the authorization is narrow: batch *recording* for
jobs already in the library, behind one confirmation naming the exact count.
Most of these tests are about what it must refuse to do.
"""

from __future__ import annotations

import pytest

from app.core.errors import ValidationError
from app.models import Job
from app.models.enums import JobSourceName, JobStatus
from app.services import applied_backfill


def _job(db, company: str, title: str, *, status=JobStatus.reviewed, suffix="") -> Job:
    job = Job(
        source=JobSourceName.boss,
        external_id=f"id{company}{title}{suffix}"[:64],
        company=company,
        title=title,
        raw_description="负责云平台运维工作。" * 5,
        normalized_description=f"{company}{title}负责云平台运维工作。" * 3,
        content_hash=f"{company}{title}{suffix}".ljust(64, "0")[:64],
        status=status,
        source_url=f"https://www.zhipin.com/job_detail/{abs(hash(company + title + suffix))}.html",
    )
    db.add(job)
    db.commit()
    return job


#: Roughly what copying BOSS's conversation list produces: names and titles
#: separated by whatever whitespace the page happens to use.
PASTED = """
阿里云   云计算技术服务工程师
    刚刚
搜狐
运维开发工程师（SRE岗）   昨天
未在库的公司   某个岗位
"""


def test_it_finds_stored_jobs_in_the_pasted_text(db):
    matched = _job(db, "阿里云", "云计算技术服务工程师")
    other = _job(db, "搜狐", "运维开发工程师（SRE岗）")
    absent = _job(db, "字节跳动", "云原生工程师")

    result = applied_backfill.plan(db, PASTED)
    ids = {m.job_id for m in result.confident}
    assert ids == {matched.id, other.id}
    assert absent.id not in ids


def test_an_entry_with_no_stored_job_is_simply_absent_and_never_created(db):
    """The paste may not create a job - that is the authorization's limit."""
    before = db.query(Job).count()
    result = applied_backfill.plan(db, PASTED)
    assert db.query(Job).count() == before
    assert result.confident == [] and result.needs_review == []
    assert any("不会被新建" in note for note in result.notes)


def test_planning_writes_nothing_at_all(db):
    job = _job(db, "阿里云", "云计算技术服务工程师")
    applied_backfill.plan(db, PASTED)
    db.refresh(job)
    assert job.status is JobStatus.reviewed
    assert db.query(Job).count() == 1


def test_a_company_only_hit_is_never_pre_selected(db):
    """Several roles at one company is normal; picking the wrong one would
    record an application that did not happen."""
    job = _job(db, "阿里云", "完全不同的岗位名称")
    result = applied_backfill.plan(db, PASTED)
    assert result.confident == []
    assert [m.job_id for m in result.needs_review] == [job.id]
    assert not result.needs_review[0].title_matched


def test_a_job_that_cannot_transition_is_reported_not_forced(db):
    """A skipped job must be restored by a human first - the workflow says so,
    and this path does not work around it."""
    job = _job(db, "阿里云", "云计算技术服务工程师", status=JobStatus.skipped)
    result = applied_backfill.plan(db, PASTED)
    assert result.confident == []
    review = next(m for m in result.needs_review if m.job_id == job.id)
    assert review.can_apply is False and "恢复待处理" in review.reason


def test_an_already_applied_job_is_listed_separately_and_not_recorded_twice(db):
    job = _job(db, "阿里云", "云计算技术服务工程师", status=JobStatus.applied)
    result = applied_backfill.plan(db, PASTED)
    assert [m.job_id for m in result.already_applied] == [job.id]
    assert result.confident == []


# --------------------------------------------------------------------------
# the confirmation gate
# --------------------------------------------------------------------------


def test_recording_without_confirmation_writes_nothing(db):
    job = _job(db, "阿里云", "云计算技术服务工程师")
    with pytest.raises(ValidationError):
        applied_backfill.confirm(db, job_ids=[job.id], confirmed=False, expected_count=1)
    db.refresh(job)
    assert job.status is JobStatus.reviewed


def test_a_count_that_does_not_match_what_was_shown_cancels_everything(db):
    """The number in the dialog is part of the confirmation. If the selection
    moved between reading it and pressing the button, none of it is recorded."""
    first = _job(db, "阿里云", "云计算技术服务工程师")
    second = _job(db, "搜狐", "运维开发工程师（SRE岗）")

    with pytest.raises(ValidationError):
        applied_backfill.confirm(
            db, job_ids=[first.id, second.id], confirmed=True, expected_count=1
        )
    for job in (first, second):
        db.refresh(job)
        assert job.status is JobStatus.reviewed, "nothing may be recorded"


def test_a_confirmed_batch_records_one_application_each(db, client):
    first = _job(db, "阿里云", "云计算技术服务工程师")
    second = _job(db, "搜狐", "运维开发工程师（SRE岗）")

    result = applied_backfill.confirm(
        db, job_ids=[first.id, second.id], confirmed=True, expected_count=2
    )
    assert sorted(result["recorded"]) == sorted([first.id, second.id])
    assert result["skipped"] == []

    for job in (first, second):
        db.refresh(job)
        assert job.status is JobStatus.applied
        events = client.get(f"/api/jobs/{job.id}/application-events").json()
        applied = [e for e in events if e["event_type"] == "applied"]
        assert len(applied) == 1, "exactly one applied event per job"


def test_one_bad_row_never_loses_the_rest(db):
    good = _job(db, "阿里云", "云计算技术服务工程师")
    blocked = _job(db, "搜狐", "运维开发工程师（SRE岗）", status=JobStatus.skipped)

    result = applied_backfill.confirm(
        db, job_ids=[good.id, blocked.id], confirmed=True, expected_count=2
    )
    assert result["recorded"] == [good.id]
    assert [row["job_id"] for row in result["skipped"]] == [blocked.id]
    db.refresh(blocked)
    assert blocked.status is JobStatus.skipped


def test_it_never_records_a_job_that_was_not_selected(db):
    selected = _job(db, "阿里云", "云计算技术服务工程师")
    bystander = _job(db, "搜狐", "运维开发工程师（SRE岗）")

    applied_backfill.confirm(db, job_ids=[selected.id], confirmed=True, expected_count=1)
    db.refresh(bystander)
    assert bystander.status is JobStatus.reviewed


def test_the_route_exposes_no_way_to_apply_only_to_record(client, db):
    """Recording is not applying. Nothing here submits anything, and there is
    no endpoint that would."""
    import app.api.routes.application as module

    source = __import__("inspect").getsource(module)
    assert "applied_backfill.confirm" in source
    for forbidden in ("立即沟通", "startchat", "submit_application", "send_greeting"):
        assert forbidden not in source


def test_an_empty_or_oversized_paste_is_refused(db):
    for bad in ("", "   ", "\n"):
        with pytest.raises(ValidationError):
            applied_backfill.plan(db, bad)
    with pytest.raises(ValidationError):
        applied_backfill.plan(db, "阿里云" * 100_000)
