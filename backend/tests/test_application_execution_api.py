"""M6 route boundary tests. Offline only; no browser or network."""

from __future__ import annotations

import hashlib

from app.models import Job, JobStatus

URL = "https://www.zhipin.com/job_detail/m6route1.html"
EXTENSION_HEADERS = {"origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop"}
SOURCE = "boss_dynamic_unverified"


def make_job(db) -> Job:
    job = Job(
        source="boss",
        external_id="m6route1",
        source_url=URL,
        company="路由测试公司",
        title="云平台工程师",
        city="北京",
        salary_text="20-30K",
        raw_description="维护云平台",
        normalized_description="维护云平台",
        content_hash=hashlib.sha256(b"m6-route").hexdigest(),
        status=JobStatus.reviewed,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def test_feature_flag_is_only_an_entry_gate(client, db, active_resume, settings, monkeypatch):
    job = make_job(db)
    payload = {"resume_id": active_resume.id, "answers_source": SOURCE, "confirmed": True}
    response = client.post(f"/api/application-approvals/jobs/{job.id}", json=payload)
    assert response.status_code == 403

    monkeypatch.setattr(settings, "human_confirmed_apply_enabled", True)
    response = client.post(f"/api/application-approvals/jobs/{job.id}", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job.id
    assert body["answers_text"] == ""
    assert body["answers_source"] == SOURCE
    assert body["state"] == "pending"


def test_create_still_requires_explicit_confirmation(client, db, active_resume, settings, monkeypatch):
    monkeypatch.setattr(settings, "human_confirmed_apply_enabled", True)
    job = make_job(db)
    response = client.post(
        f"/api/application-approvals/jobs/{job.id}",
        json={"resume_id": active_resume.id, "answers_source": SOURCE, "confirmed": False},
    )
    assert response.status_code == 422


def test_validation_and_outcome_require_the_extension_origin(client, db, active_resume, settings, monkeypatch):
    monkeypatch.setattr(settings, "human_confirmed_apply_enabled", True)
    job = make_job(db)
    created = client.post(
        f"/api/application-approvals/jobs/{job.id}",
        json={"resume_id": active_resume.id, "answers_source": SOURCE, "confirmed": True},
    ).json()
    identity = {"observed_url": URL, "observed_external_id": "m6route1"}

    assert client.post(
        f"/api/application-approvals/{created['id']}/validate", json=identity
    ).status_code == 403
    checked = client.post(
        f"/api/application-approvals/{created['id']}/validate",
        json=identity,
        headers=EXTENSION_HEADERS,
    )
    assert checked.status_code == 200
    assert checked.json()["ok"] is True

    assert client.post(
        f"/api/application-approvals/{created['id']}/begin", json=identity
    ).status_code == 403
    begun = client.post(
        f"/api/application-approvals/{created['id']}/begin",
        json=identity,
        headers=EXTENSION_HEADERS,
    )
    assert begun.status_code == 200
    assert begun.json()["state"] == "executing"
    assert begun.json()["attempt_started_at"] is not None

    assert client.post(
        f"/api/application-approvals/{created['id']}/outcome",
        json={**identity, "outcome": "unknown"},
    ).status_code == 403
    recorded = client.post(
        f"/api/application-approvals/{created['id']}/outcome",
        json={**identity, "outcome": "unknown", "detail": "站点结果不可核实"},
        headers=EXTENSION_HEADERS,
    )
    assert recorded.status_code == 200
    assert recorded.json()["state"] == "consumed"
    assert recorded.json()["outcome"] == "unknown"
    db.refresh(job)
    assert job.status is JobStatus.reviewed

    # The same approval can never claim a second browser attempt.
    assert client.post(
        f"/api/application-approvals/{created['id']}/begin",
        json=identity,
        headers=EXTENSION_HEADERS,
    ).status_code == 422


def test_route_has_no_plural_job_or_batch_payload(client, db, active_resume, settings, monkeypatch):
    monkeypatch.setattr(settings, "human_confirmed_apply_enabled", True)
    job = make_job(db)
    response = client.post(
        f"/api/application-approvals/jobs/{job.id}",
        json={"job_ids": [job.id], "resume_id": active_resume.id, "confirmed": True},
    )
    assert response.status_code == 422
    assert client.post("/api/application-approvals/batch", json={"job_ids": [job.id]}).status_code in {404, 405, 422}


def test_legacy_or_guessed_greeting_payload_is_rejected(client, db, active_resume, settings, monkeypatch):
    monkeypatch.setattr(settings, "human_confirmed_apply_enabled", True)
    job = make_job(db)
    for payload in (
        {"resume_id": active_resume.id, "answers_text": "猜测正文", "answers_source": SOURCE, "confirmed": True},
        {"resume_id": active_resume.id, "answers_source": "human_attested_unverified", "confirmed": True},
    ):
        response = client.post(f"/api/application-approvals/jobs/{job.id}", json=payload)
        assert response.status_code == 422
