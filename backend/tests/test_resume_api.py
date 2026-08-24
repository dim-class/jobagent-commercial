"""Resume variant HTTP surface (v0.7).

Includes the cost gate on 比较简历 - the one action in v0.7 that may spend
OpenAI credits. Every test here patches the agent; nothing makes a real call.
"""

from __future__ import annotations

import pytest

from app.models import JobStatus, Resume, ResumeUsage
from app.services import resume_variants
from app.services.application_cycles import effective_cycle

from tests.test_analysis import build_result
from tests.test_career_analytics import NOW, add_analysis, make_job
from tests.test_resume_variants import apply_with, make_resume


@pytest.fixture
def fake_agent(monkeypatch):
    """Patch the agent and count how many times the model was invoked."""
    calls: list[dict] = []

    async def _fake_run_job_match(**kwargs):
        calls.append(kwargs)
        return build_result()

    monkeypatch.setattr("app.services.job_matcher.run_job_match", _fake_run_job_match)
    return calls


# --------------------------------------------------------------------------
# listing / lifecycle
# --------------------------------------------------------------------------


def test_listing_resumes_shows_variant_metadata(client, db):
    make_resume(db, variant_name="Cloud版", variant_group="cloud", active=True)

    body = client.get("/api/resumes").json()
    assert body[0]["label"] == "Cloud版"
    assert body[0]["variant_group"] == "cloud"
    assert body[0]["archived"] is False


def test_listing_resumes_reports_real_performance(client, db):
    resume = make_resume(db, variant_name="DevOps版")
    job = make_job(db, status=JobStatus.new)
    apply_with(db, job, resume)

    body = client.get("/api/resumes").json()
    row = next(r for r in body if r["id"] == resume.id)
    assert row["applications"] == 1
    assert row["replies"] == 0


def test_archived_resumes_are_hidden_unless_requested(client, db):
    keep = make_resume(db, variant_name="DevOps版")
    retire = make_resume(db, variant_name="旧版")
    resume_variants.archive(db, retire.id)

    assert [r["id"] for r in client.get("/api/resumes").json()] == [keep.id]
    everything = client.get("/api/resumes?include_archived=true").json()
    assert {r["id"] for r in everything} == {keep.id, retire.id}


def test_renaming_a_variant(client, db):
    resume = make_resume(db, variant_name="Cloud版")

    body = client.patch(
        f"/api/resumes/{resume.id}", json={"variant_name": "Cloud版 v2", "notes": "强调 AWS"}
    ).json()

    assert body["label"] == "Cloud版 v2"
    assert body["notes"] == "强调 AWS"


def test_cloning_a_variant(client, db):
    source = make_resume(db, variant_name="Cloud版", active=True)

    response = client.post(
        f"/api/resumes/{source.id}/clone", json={"variant_name": "Cloud版 v2"}
    )
    assert response.status_code == 201

    body = response.json()
    assert body["parent_resume_id"] == source.id
    assert body["is_active"] is False
    assert body["label"] == "Cloud版 v2"


def test_archiving_and_unarchiving_over_http(client, db):
    resume = make_resume(db, variant_name="旧版")

    assert client.post(f"/api/resumes/{resume.id}/archive").json()["archived"] is True
    assert client.post(f"/api/resumes/{resume.id}/unarchive").json()["archived"] is False


def test_archiving_the_active_resume_is_refused(client, db):
    resume = make_resume(db, variant_name="Cloud版", active=True)
    response = client.post(f"/api/resumes/{resume.id}/archive")
    assert response.status_code == 422
    assert "当前的 AI 分析简历" in response.json()["message"]


def test_there_is_no_delete_endpoint(client, db):
    """Archiving replaces deletion, so attribution can never be orphaned."""
    resume = make_resume(db)
    assert client.delete(f"/api/resumes/{resume.id}").status_code in (404, 405)


def test_resume_performance_endpoint(client, db):
    resume = make_resume(db, variant_name="DevOps版")
    job = make_job(db, status=JobStatus.new)
    apply_with(db, job, resume)

    body = client.get(f"/api/resumes/{resume.id}/performance").json()
    assert body["label"] == "DevOps版"
    assert body["applications"] == 1


# --------------------------------------------------------------------------
# mark applied
# --------------------------------------------------------------------------


def test_mark_applied_accepts_the_resume_actually_used(client, db):
    make_resume(db, variant_name="Cloud版", active=True)
    submitted = make_resume(db, variant_name="DevOps版")
    job = make_job(db, status=JobStatus.new)

    response = client.post(
        f"/api/jobs/{job.id}/mark-applied",
        json={"confirmed": True, "resume_id": submitted.id},
    )
    assert response.status_code == 200

    db.expire_all()
    job = db.get(type(job), job.id)
    assert effective_cycle(job).resume_id == submitted.id


def test_mark_applied_accepts_no_resume(client, db):
    make_resume(db, variant_name="Cloud版", active=True)
    job = make_job(db, status=JobStatus.new)

    client.post(
        f"/api/jobs/{job.id}/mark-applied",
        json={"confirmed": True, "resume_usage": "no_resume"},
    )

    db.expire_all()
    job = db.get(type(job), job.id)
    cycle = effective_cycle(job)
    assert cycle.resume_usage is ResumeUsage.no_resume
    assert cycle.resume_id is None


def test_mark_applied_with_an_archived_resume_is_refused(client, db):
    resume = make_resume(db, variant_name="旧版")
    resume_variants.archive(db, resume.id)
    job = make_job(db, status=JobStatus.new)

    response = client.post(
        f"/api/jobs/{job.id}/mark-applied", json={"confirmed": True, "resume_id": resume.id}
    )
    assert response.status_code == 422


def test_claiming_a_resume_was_used_without_naming_it_is_rejected(client, db):
    job = make_job(db, status=JobStatus.new)
    response = client.post(
        f"/api/jobs/{job.id}/mark-applied", json={"confirmed": True, "resume_usage": "used"}
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------
# historical attribution
# --------------------------------------------------------------------------


def test_the_unattributed_worklist_is_exposed(client, db):
    make_resume(db, variant_name="Cloud版", active=True)
    job = make_job(db, status=JobStatus.new, company="缺记录公司")
    client.post(f"/api/jobs/{job.id}/mark-applied", json={"confirmed": True})

    body = client.get("/api/analytics/resumes/unattributed").json()
    assert body["total"] == 1
    assert body["items"][0]["company"] == "缺记录公司"
    assert "不会被自动归属" in body["message"]


def test_attributing_a_historical_application_over_http(client, db):
    resume = make_resume(db, variant_name="Cloud版")
    job = make_job(db, status=JobStatus.new)
    client.post(f"/api/jobs/{job.id}/mark-applied", json={"confirmed": True})

    response = client.post(
        f"/api/jobs/{job.id}/attribute-resume", json={"resume_id": resume.id}
    )
    assert response.status_code == 200

    db.expire_all()
    job = db.get(type(job), job.id)
    assert effective_cycle(job).resume_id == resume.id

    assert client.get("/api/analytics/resumes/unattributed").json()["total"] == 0


def test_the_attribution_appears_in_the_event_history(client, db):
    resume = make_resume(db, variant_name="Cloud版")
    job = make_job(db, status=JobStatus.new)
    client.post(f"/api/jobs/{job.id}/mark-applied", json={"confirmed": True})
    client.post(f"/api/jobs/{job.id}/attribute-resume", json={"resume_id": resume.id})

    events = client.get(f"/api/jobs/{job.id}/application-events").json()
    kinds = [e["event_type"] for e in events]
    assert "application_resume_attributed" in kinds
    assert kinds.count("applied") == 1, "the original applied event is intact"


# --------------------------------------------------------------------------
# analytics endpoint
# --------------------------------------------------------------------------


def test_resume_analytics_endpoint_on_an_empty_database(client):
    body = client.get("/api/analytics/resumes").json()
    assert body["by_resume"] == []
    assert body["attribution_coverage"]["total"] == 0
    assert "观察性数据" in body["observational_warning"]


def test_resume_analytics_endpoint_reports_variants(client, db):
    from tests.test_resume_analytics import seed_cohort

    devops = make_resume(db, variant_name="DevOps版")
    cloud = make_resume(db, variant_name="Cloud版")
    seed_cohort(db, devops, applications=18, replies=8, interviews=5)
    seed_cohort(db, cloud, applications=15, replies=5, interviews=3)

    body = client.get("/api/analytics/resumes?window=all").json()
    assert body["by_resume"][0]["label"] == "DevOps版"
    assert body["by_resume"][0]["mature_reply_rate"]["denominator"] == 18
    assert body["attribution_coverage"]["ratio"] == 1.0


def test_resume_analytics_supports_filters(client, db):
    devops = make_resume(db, variant_name="DevOps版")
    job = make_job(db, status=JobStatus.new, city="杭州")
    apply_with(db, job, devops)
    other = make_job(db, status=JobStatus.new, city="北京")
    apply_with(db, other, devops)

    body = client.get("/api/analytics/resumes?window=all&city=杭州").json()
    assert body["summary"]["applications"] == 1
    assert body["filters"]["city"] == "杭州"


# --------------------------------------------------------------------------
# analyze with a chosen variant
# --------------------------------------------------------------------------


def test_analyzing_with_a_specific_resume(client, db, fake_agent):
    active = make_resume(db, variant_name="Cloud版", active=True)
    other = make_resume(db, variant_name="DevOps版")
    job = make_job(db, status=JobStatus.new)

    body = client.post(f"/api/jobs/{job.id}/analyze-with-resume/{other.id}").json()

    assert body["meta"]["resume_id"] == other.id
    assert len(fake_agent) == 1

    db.expire_all()
    assert db.get(Resume, active.id).is_active is True, "the active resume is unchanged"


def test_analyzing_with_a_variant_reuses_its_cache(client, db, fake_agent):
    resume = make_resume(db, variant_name="DevOps版")
    job = make_job(db, status=JobStatus.new)

    client.post(f"/api/jobs/{job.id}/analyze-with-resume/{resume.id}")
    client.post(f"/api/jobs/{job.id}/analyze-with-resume/{resume.id}")

    assert len(fake_agent) == 1, "the second call came from cache"


def test_resume_analyses_endpoint_never_calls_the_model(client, db, fake_agent):
    cloud = make_resume(db, variant_name="Cloud版")
    devops = make_resume(db, variant_name="DevOps版")
    job = make_job(db, status=JobStatus.new)
    add_analysis(db, job, cloud, score=84)
    add_analysis(db, job, devops, score=91)

    body = client.get(f"/api/jobs/{job.id}/resume-analyses").json()

    assert [c["overall_score"] for c in body["cells"]] == [91, 84]
    assert fake_agent == [], "reading scores costs nothing"


def test_resume_analyses_shows_the_applied_variant(client, db):
    cloud = make_resume(db, variant_name="Cloud版", active=True)
    devops = make_resume(db, variant_name="DevOps版")
    job = make_job(db, status=JobStatus.new)
    add_analysis(db, job, cloud, score=84)
    apply_with(db, job, devops)

    body = client.get(f"/api/jobs/{job.id}/resume-analyses").json()
    assert body["applied_resume_id"] == devops.id
    assert body["active_analysis_resume_id"] == cloud.id


# --------------------------------------------------------------------------
# compare resumes - the cost gate
# --------------------------------------------------------------------------


def test_comparing_uncached_variants_needs_confirmation(client, db, fake_agent):
    cloud = make_resume(db, variant_name="Cloud版")
    devops = make_resume(db, variant_name="DevOps版")
    job = make_job(db, status=JobStatus.new)

    response = client.post(
        f"/api/jobs/{job.id}/compare-resumes",
        json={"resume_ids": [cloud.id, devops.id], "confirmed": False},
    )

    assert response.status_code == 422
    body = response.json()
    assert "可能产生 API 费用" in body["message"]
    assert body["detail"]["pending_analyses"] == 2
    assert fake_agent == [], "nothing was spent"


def test_a_confirmed_comparison_analyzes_the_missing_variants(client, db, fake_agent):
    cloud = make_resume(db, variant_name="Cloud版")
    devops = make_resume(db, variant_name="DevOps版")
    job = make_job(db, status=JobStatus.new)

    body = client.post(
        f"/api/jobs/{job.id}/compare-resumes",
        json={"resume_ids": [cloud.id, devops.id], "confirmed": True},
    ).json()

    assert len(fake_agent) == 2
    assert body["api_calls_made"] == 2
    assert len(body["cells"]) == 2
    assert all(c["newly_analyzed"] for c in body["cells"])


def test_a_comparison_never_re_analyzes_a_cached_variant(client, db, fake_agent):
    cloud = make_resume(db, variant_name="Cloud版")
    devops = make_resume(db, variant_name="DevOps版")
    job = make_job(db, status=JobStatus.new)

    client.post(f"/api/jobs/{job.id}/analyze-with-resume/{cloud.id}")
    assert len(fake_agent) == 1

    body = client.post(
        f"/api/jobs/{job.id}/compare-resumes",
        json={"resume_ids": [cloud.id, devops.id], "confirmed": True},
    ).json()

    assert len(fake_agent) == 2, "only the uncached variant cost a call"
    assert body["api_calls_made"] == 1
    cached_cell = next(c for c in body["cells"] if c["resume_id"] == cloud.id)
    assert cached_cell["newly_analyzed"] is False


def test_a_fully_cached_comparison_costs_nothing_and_says_so(client, db, fake_agent):
    cloud = make_resume(db, variant_name="Cloud版")
    devops = make_resume(db, variant_name="DevOps版")
    job = make_job(db, status=JobStatus.new)
    client.post(f"/api/jobs/{job.id}/analyze-with-resume/{cloud.id}")
    client.post(f"/api/jobs/{job.id}/analyze-with-resume/{devops.id}")
    calls_before = len(fake_agent)

    body = client.post(
        f"/api/jobs/{job.id}/compare-resumes",
        json={"resume_ids": [cloud.id, devops.id], "confirmed": False},
    ).json()

    assert len(fake_agent) == calls_before, "no confirmation needed, nothing to spend"
    assert body["api_calls_made"] == 0
    assert "未产生费用" in body["message"]


def test_comparison_rejects_an_empty_selection(client, db):
    job = make_job(db, status=JobStatus.new)
    response = client.post(
        f"/api/jobs/{job.id}/compare-resumes", json={"resume_ids": [], "confirmed": True}
    )
    assert response.status_code == 422


def test_comparison_caps_how_many_variants_at_once(client, db):
    job = make_job(db, status=JobStatus.new)
    ids = [make_resume(db, variant_name=f"版本{i}").id for i in range(6)]

    response = client.post(
        f"/api/jobs/{job.id}/compare-resumes", json={"resume_ids": ids, "confirmed": True}
    )
    assert response.status_code == 422
