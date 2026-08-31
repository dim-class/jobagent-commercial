from __future__ import annotations

import hashlib

import pytest

from app.core.errors import ValidationError
from app.models import Job
from app.services import salary_backfill


def add_job(db, number: int, *, salary: str | None = None, source: str = "boss",
            url: str | None = None) -> Job:
    external_id = f"job{number}"
    job = Job(
        source=source, external_id=external_id,
        source_url=url or f"https://www.zhipin.com/job_detail/{external_id}.html",
        company=f"公司{number}", title=f"岗位{number}", city="北京", salary_text=salary,
        raw_description=f"JD {number}", normalized_description=f"jd {number}",
        content_hash=hashlib.sha256(f"job-{number}".encode()).hexdigest(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def test_plan_only_includes_canonical_boss_jobs_missing_salary(db):
    eligible = add_job(db, 1)
    add_job(db, 2, salary="20-30K")
    add_job(db, 3, source="manual")
    add_job(db, 4, url="https://www.zhipin.com/job_detail/job4.html?x=1")
    value = salary_backfill.plan(db)
    assert value["total_jobs"] == 4
    assert value["salary_present"] == 1
    assert value["salary_missing"] == 3
    assert [job.id for job in value["items"]] == [eligible.id]
    assert value["ineligible_jobs"] == 2


def test_run_requires_exact_fingerprint_and_pauses_every_three(db):
    jobs = [add_job(db, i) for i in range(1, 5)]
    value = salary_backfill.plan(db)
    run = salary_backfill.create_run(
        db, job_ids=[job.id for job in jobs], expected_fingerprint=value["fingerprint"],
        confirmed=True,
    )
    run = salary_backfill.start_or_resume(db, run.id)
    for index in range(3):
        run = salary_backfill.claim_next(db, run.id)
        current = run.current_job_id
        assert current == jobs[index].id
        run = salary_backfill.record_item(
            db, run.id, job_id=current, outcome="unavailable", reason="salary_unreadable")
    assert run.state == "paused"
    assert run.paused_reason == "batch_limit"
    assert run.processed_jobs == 3
    assert run.session_processed == 3

    run = salary_backfill.start_or_resume(db, run.id)
    assert run.session_processed == 0
    run = salary_backfill.claim_next(db, run.id)
    jobs[3].salary_text = "18-25K"
    db.commit()
    run = salary_backfill.record_item(db, run.id, job_id=jobs[3].id, outcome="updated", reason=None)
    assert run.state == "completed"
    assert run.updated_jobs == 1
    assert run.unavailable_jobs == 3


def test_explicit_remaining_authorization_is_finite_and_run_scoped(db):
    jobs = [add_job(db, i) for i in range(1, 6)]
    value = salary_backfill.plan(db)
    run = salary_backfill.create_run(
        db, job_ids=[job.id for job in jobs], expected_fingerprint=value["fingerprint"],
        confirmed=True,
    )
    run = salary_backfill.start_or_resume(db, run.id)
    for job in jobs[:3]:
        run = salary_backfill.claim_next(db, run.id)
        run = salary_backfill.record_item(
            db, run.id, job_id=job.id, outcome="unavailable", reason="salary_unreadable")
    assert run.state == "paused"
    assert run.session_cap == 3

    run = salary_backfill.authorize_remaining(db, run.id, confirmed=True)
    assert run.session_cap == 2
    assert run.last_action == "remaining_authorized"
    run = salary_backfill.start_or_resume(db, run.id)
    for job in jobs[3:]:
        run = salary_backfill.claim_next(db, run.id)
        run = salary_backfill.record_item(
            db, run.id, job_id=job.id, outcome="unavailable", reason="salary_unreadable")
    assert run.state == "completed"
    assert run.processed_jobs == 5


def test_authorize_remaining_api_requires_confirmation(client, db):
    jobs = [add_job(db, i) for i in range(1, 5)]
    plan = client.get("/api/jobs/salary-backfill/plan").json()
    created = client.post("/api/jobs/salary-backfill/runs", json={
        "confirmed": True, "fingerprint": plan["fingerprint"],
        "job_ids": [job.id for job in jobs],
    }).json()
    run_id = created["id"]
    client.post(f"/api/jobs/salary-backfill/runs/{run_id}/start")
    for job in jobs[:3]:
        client.post(f"/api/jobs/salary-backfill/runs/{run_id}/claim")
        client.post(f"/api/jobs/salary-backfill/runs/{run_id}/item", json={
            "job_id": job.id, "outcome": "unavailable", "reason": "salary_unreadable",
        })
    rejected = client.post(
        f"/api/jobs/salary-backfill/runs/{run_id}/authorize-remaining",
        json={"confirmed": False},
    )
    assert rejected.status_code == 422
    authorized = client.post(
        f"/api/jobs/salary-backfill/runs/{run_id}/authorize-remaining",
        json={"confirmed": True},
    )
    assert authorized.status_code == 200
    assert authorized.json()["session_cap"] == 1


def test_updated_requires_salary_and_api_is_loopback(client, db):
    job = add_job(db, 9)
    plan = client.get("/api/jobs/salary-backfill/plan").json()
    created = client.post("/api/jobs/salary-backfill/runs", json={
        "confirmed": True, "fingerprint": plan["fingerprint"], "job_ids": [job.id],
    })
    assert created.status_code == 200
    run_id = created.json()["id"]
    assert client.post(f"/api/jobs/salary-backfill/runs/{run_id}/start").status_code == 200
    assert client.post(f"/api/jobs/salary-backfill/runs/{run_id}/claim").json()["current_job_id"] == job.id
    failed = client.post(f"/api/jobs/salary-backfill/runs/{run_id}/item", json={
        "job_id": job.id, "outcome": "updated",
    })
    assert failed.status_code == 422
    paused = client.post(f"/api/jobs/salary-backfill/runs/{run_id}/pause", json={
        "reason": "login_required",
    })
    assert paused.json()["paused_reason"] == "login_required"


def test_a_pending_run_can_be_authorized_for_all_jobs_before_the_first_start(db):
    """One click up front: 18 jobs as one session, not six three-job batches."""
    jobs = [add_job(db, i) for i in range(1, 8)]
    value = salary_backfill.plan(db)
    run = salary_backfill.create_run(
        db, job_ids=[job.id for job in jobs], expected_fingerprint=value["fingerprint"],
        confirmed=True,
    )
    assert run.state == "pending"
    assert run.session_cap == 3

    run = salary_backfill.authorize_remaining(db, run.id, confirmed=True)
    assert run.session_cap == 7
    assert run.last_action == "remaining_authorized"

    run = salary_backfill.start_or_resume(db, run.id)
    for job in jobs:
        run = salary_backfill.claim_next(db, run.id)
        assert run.current_job_id == job.id, "never pauses at the default 3"
        run = salary_backfill.record_item(
            db, run.id, job_id=job.id, outcome="unavailable", reason="salary_unreadable")
    assert run.state == "completed"
    assert run.processed_jobs == 7
    assert run.paused_reason is None


def test_a_larger_cap_still_stops_on_every_existing_stop_condition(db):
    """A raised cap buys continuity, never permission."""
    jobs = [add_job(db, i) for i in range(1, 6)]
    value = salary_backfill.plan(db)
    run = salary_backfill.create_run(
        db, job_ids=[job.id for job in jobs], expected_fingerprint=value["fingerprint"],
        confirmed=True,
    )
    run = salary_backfill.authorize_remaining(db, run.id, confirmed=True)
    run = salary_backfill.start_or_resume(db, run.id)
    run = salary_backfill.claim_next(db, run.id)
    run = salary_backfill.record_item(
        db, run.id, job_id=jobs[0].id, outcome="unavailable", reason="salary_unreadable")

    run = salary_backfill.pause(db, run.id, reason="login_required")
    assert run.state == "paused"
    assert run.paused_reason == "login_required"
    assert run.processed_jobs == 1, "a raised cap does not keep running through a pause"


def test_authorizing_all_needs_confirmation_and_a_run_with_no_job_in_flight(db):
    jobs = [add_job(db, i) for i in range(1, 4)]
    value = salary_backfill.plan(db)
    run = salary_backfill.create_run(
        db, job_ids=[job.id for job in jobs], expected_fingerprint=value["fingerprint"],
        confirmed=True,
    )
    with pytest.raises(ValidationError):
        salary_backfill.authorize_remaining(db, run.id, confirmed=False)

    run = salary_backfill.start_or_resume(db, run.id)
    run = salary_backfill.claim_next(db, run.id)
    assert run.current_job_id is not None
    with pytest.raises(ValidationError):
        salary_backfill.authorize_remaining(db, run.id, confirmed=True)
    assert salary_backfill.get_run(db, run.id).session_cap == 3
