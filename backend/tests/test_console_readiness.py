"""What a fresh installation still needs, named rather than implied.

Every "the button does nothing" this project produced for a new pair of hands
was one unmet prerequisite the page never mentioned. This endpoint exists to
say which one, and it must stay free: counts and configuration only, no model
call, no browser work, nothing written.
"""

from __future__ import annotations


def test_a_fresh_install_names_what_is_missing(client, db):
    body = client.get("/api/console/readiness").json()

    assert body["ready"] is False
    missing = {check["key"]: check for check in body["checks"] if not check["ok"]}
    assert "resume" in missing, "no résumé has been uploaded yet"
    # Every blocker carries an instruction, not just a red mark.
    assert all(check["fix"] for check in missing.values())


def test_it_becomes_ready_once_the_prerequisites_exist(client, active_resume):
    body = client.get("/api/console/readiness").json()

    assert body["ready"] is True, [c for c in body["checks"] if not c["ok"]]
    assert body["version"]
    labels = {check["key"] for check in body["checks"]}
    assert {"resume", "directions", "openai", "library", "apply"} <= labels


def test_it_writes_nothing_and_calls_no_model(client, active_resume, db, monkeypatch):
    from app.models import Job
    from app.services import job_matcher

    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("readiness must never reach the model")

    monkeypatch.setattr(job_matcher, "run_job_match", explode)
    before = db.query(Job).count()
    client.get("/api/console/readiness")
    db.expire_all()
    assert db.query(Job).count() == before


def test_the_library_row_counts_what_has_not_been_analysed(client, active_resume, db):
    from app.models import Job
    from app.models.enums import JobSourceName, JobStatus

    db.add(
        Job(
            source=JobSourceName.manual,
            company="某公司",
            title="云平台工程师",
            raw_description="负责平台。" * 5,
            normalized_description="负责平台。" * 5,
            content_hash="f" * 64,
            status=JobStatus.new,
        )
    )
    db.commit()

    row = next(c for c in client.get("/api/console/readiness").json()["checks"] if c["key"] == "library")
    assert "1 个还没分析" in row["detail"]
