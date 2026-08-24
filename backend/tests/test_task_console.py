"""求职任务控制台 (M1) - service and API tests.

Covers the acceptance criteria from docs/orchestration/ROADMAP.md: criteria
round-trip (including the None-vs-[] distinction for exclusions), the same
Job attached to two tasks without duplicating it, idempotent association,
the max_candidates cap applying only to a genuinely new association, real
analysis-or-absence on candidate listing, and that nothing here ever calls
the model or changes Job.status.
"""

from __future__ import annotations

from app.models import Job, JobAnalysis, JobStatus, Verdict
from app.services import task_console

# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------

_counter = 0


def make_job(db, *, title: str = "云计算工程师", company: str = "示例科技") -> Job:
    global _counter
    _counter += 1
    job = Job(
        source="manual",
        external_id=f"ext-{_counter}",
        company=company,
        title=title,
        city="北京",
        salary_text="20k-30k",
        raw_description="岗位职责：负责云平台运维。",
        normalized_description="岗位职责：负责云平台运维。",
        content_hash=f"hash-{_counter:06d}",
        status=JobStatus.new,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def make_analysis(
    db, job: Job, resume_id: int, *, score: int = 70, verdict: Verdict = Verdict.maybe
) -> JobAnalysis:
    global _counter
    _counter += 1
    analysis = JobAnalysis(
        job_id=job.id,
        resume_id=resume_id,
        model="test-model",
        prompt_version="v1",
        cache_key=f"cache-{_counter:06d}",
        overall_score=score,
        verdict=verdict,
        result_json={"overall_score": score, "verdict": verdict.value},
    )
    db.add(analysis)
    db.commit()
    db.refresh(analysis)
    return analysis


def full_task_payload(**overrides) -> dict:
    payload = {
        "name": "云计算运维-北京",
        "keywords": "云计算 运维",
        "city": "北京",
        "experience_text": "3-5年",
        "education_text": "本科",
        "salary_min": 20000,
        "salary_max": 40000,
        "exclusions": ["外包", "某黑名单公司"],
        "max_candidates": 10,
        "min_score": 60,
        "notes": "测试任务",
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------
# criteria round-trip
# --------------------------------------------------------------------------


def test_creating_a_task_persists_the_submitted_criteria(db):
    task = task_console.create_task(db, **{k: v for k, v in full_task_payload().items()})
    db.expire_all()
    reloaded = task_console.get_task(db, task.id)

    assert reloaded.name == "云计算运维-北京"
    assert reloaded.keywords == "云计算 运维"
    assert reloaded.city == "北京"
    assert reloaded.experience_text == "3-5年"
    assert reloaded.education_text == "本科"
    assert reloaded.salary_min == 20000
    assert reloaded.salary_max == 40000
    assert reloaded.exclusions_json == ["外包", "某黑名单公司"]
    assert reloaded.max_candidates == 10
    assert reloaded.min_score == 60
    assert reloaded.mode.value == "manual_review_only"
    assert reloaded.notes == "测试任务"


def test_omitted_optional_exclusions_stays_absent_not_empty(db):
    """None (never set) and [] (explicitly no exclusions) must stay distinct."""
    never_set = task_console.create_task(db, name="未设置排除项")
    explicit_empty = task_console.create_task(db, name="明确无排除项", exclusions=[])

    db.expire_all()
    assert task_console.get_task(db, never_set.id).exclusions_json is None
    assert task_console.get_task(db, explicit_empty.id).exclusions_json == []


def test_other_omitted_optionals_also_stay_none(db):
    task = task_console.create_task(db, name="仅名称")
    db.expire_all()
    reloaded = task_console.get_task(db, task.id)

    assert reloaded.keywords is None
    assert reloaded.city is None
    assert reloaded.salary_min is None
    assert reloaded.salary_max is None
    assert reloaded.resume_id is None
    assert reloaded.max_candidates is None
    assert reloaded.min_score is None


def test_task_api_round_trips_criteria_including_exclusions_distinction(client):
    created = client.post("/api/tasks", json=full_task_payload()).json()
    assert created["exclusions"] == ["外包", "某黑名单公司"]
    assert created["salary_min"] == 20000
    assert created["mode"] == "manual_review_only"

    never_set = client.post("/api/tasks", json={"name": "未设置"}).json()
    assert never_set["exclusions"] is None

    explicit_empty = client.post(
        "/api/tasks", json={"name": "明确为空", "exclusions": []}
    ).json()
    assert explicit_empty["exclusions"] == []


def test_salary_range_validation_rejects_min_greater_than_max(client):
    resp = client.post(
        "/api/tasks", json={"name": "非法范围", "salary_min": 50000, "salary_max": 10000}
    )
    assert resp.status_code == 422


def test_creating_a_task_never_searches_or_captures_anything(client, db):
    """A task is configuration only - it must not create any Job row."""
    before = db.query(Job).count()
    client.post("/api/tasks", json=full_task_payload())
    assert db.query(Job).count() == before


# --------------------------------------------------------------------------
# many-to-many candidate association
# --------------------------------------------------------------------------


def test_the_same_job_can_belong_to_two_tasks(db):
    job = make_job(db)
    task_a = task_console.create_task(db, name="任务A")
    task_b = task_console.create_task(db, name="任务B")

    task_console.add_candidate(db, task_a.id, job.id)
    task_console.add_candidate(db, task_b.id, job.id)

    candidates_a = task_console.list_candidates(db, task_a.id)
    candidates_b = task_console.list_candidates(db, task_b.id)
    assert [c.job_id for c in candidates_a] == [job.id]
    assert [c.job_id for c in candidates_b] == [job.id]
    # Still exactly one Job row - association never duplicates or re-owns it.
    assert db.query(Job).filter(Job.id == job.id).count() == 1


def test_attaching_the_same_job_twice_is_idempotent(db):
    job = make_job(db)
    task = task_console.create_task(db, name="任务")

    first = task_console.add_candidate(db, task.id, job.id)
    second = task_console.add_candidate(db, task.id, job.id)

    assert first.id == second.id
    candidates = task_console.list_candidates(db, task.id)
    assert len(candidates) == 1


def test_max_candidates_blocks_a_new_association_but_not_reassociation(db):
    from app.core.errors import ValidationError

    job_a = make_job(db, title="岗位A")
    job_b = make_job(db, title="岗位B")
    task = task_console.create_task(db, name="有上限的任务", max_candidates=1)

    task_console.add_candidate(db, task.id, job_a.id)

    try:
        task_console.add_candidate(db, task.id, job_b.id)
        raised = False
    except ValidationError:
        raised = True
    assert raised, "a genuinely new candidate over the cap must be rejected"

    # Re-associating the same job that is already counted must still work.
    again = task_console.add_candidate(db, task.id, job_a.id)
    assert again.job_id == job_a.id
    assert len(task_console.list_candidates(db, task.id)) == 1


def test_max_candidates_enforced_through_the_api(client, db):
    job_a = make_job(db, title="岗位A")
    job_b = make_job(db, title="岗位B")
    task_id = client.post(
        "/api/tasks", json={"name": "上限任务", "max_candidates": 1}
    ).json()["id"]

    ok = client.post(f"/api/tasks/{task_id}/candidates", json={"job_id": job_a.id})
    assert ok.status_code == 200

    blocked = client.post(f"/api/tasks/{task_id}/candidates", json={"job_id": job_b.id})
    assert blocked.status_code == 422

    # The already-counted job can still be re-associated (idempotent).
    again = client.post(f"/api/tasks/{task_id}/candidates", json={"job_id": job_a.id})
    assert again.status_code == 200


# --------------------------------------------------------------------------
# candidate listing: real analysis, or honestly absent
# --------------------------------------------------------------------------


def test_candidate_listing_reports_no_analysis_as_none(client, db):
    job = make_job(db)
    task = task_console.create_task(db, name="任务")
    task_console.add_candidate(db, task.id, job.id)

    body = client.get(f"/api/tasks/{task.id}/candidates").json()
    assert body["items"][0]["analysis"] is None


def test_candidate_listing_reports_the_current_real_analysis(client, db, active_resume):
    job = make_job(db)
    task = task_console.create_task(db, name="任务")
    task_console.add_candidate(db, task.id, job.id)

    make_analysis(db, job, active_resume.id, score=55, verdict=Verdict.maybe)
    newest = make_analysis(db, job, active_resume.id, score=88, verdict=Verdict.strong_apply)

    body = client.get(f"/api/tasks/{task.id}/candidates").json()
    analysis = body["items"][0]["analysis"]
    assert analysis is not None
    assert analysis["overall_score"] == newest.overall_score == 88
    assert analysis["verdict"] == "strong_apply"


# --------------------------------------------------------------------------
# what it must never do
# --------------------------------------------------------------------------


def test_task_console_makes_no_openai_call(client, db, monkeypatch):
    import app.services.job_matcher as job_matcher

    def _explode(*args, **kwargs):  # pragma: no cover
        raise AssertionError("task console must never call the model")

    monkeypatch.setattr(job_matcher, "run_job_match", _explode)

    job = make_job(db)
    task_id = client.post("/api/tasks", json=full_task_payload()).json()["id"]
    client.post(f"/api/tasks/{task_id}/candidates", json={"job_id": job.id})
    client.get(f"/api/tasks/{task_id}/candidates")
    client.get("/api/tasks")


def test_task_console_never_changes_job_status(client, db):
    job = make_job(db)
    assert job.status == JobStatus.new

    task_id = client.post("/api/tasks", json=full_task_payload()).json()["id"]
    client.post(f"/api/tasks/{task_id}/candidates", json={"job_id": job.id})
    client.get(f"/api/tasks/{task_id}/candidates")

    db.expire_all()
    assert db.get(Job, job.id).status == JobStatus.new


def test_a_missing_task_404s(client):
    resp = client.get("/api/tasks/999999")
    assert resp.status_code == 404


def test_a_missing_job_404s_on_association(client, db):
    task_id = client.post("/api/tasks", json={"name": "任务"}).json()["id"]
    resp = client.post(f"/api/tasks/{task_id}/candidates", json={"job_id": 999999})
    assert resp.status_code == 404
