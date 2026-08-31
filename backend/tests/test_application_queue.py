"""The daily queue: eligibility, sorting, filters, and the AI/human boundary.

The most important thing asserted here is that an AI verdict is a
*recommendation* and ``Job.status`` is a *human decision* - a model saying
"skip" must never mark a job skipped.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import Job, JobAnalysis, JobStatus, Verdict
from app.schemas.application import ProposalState
from app.services import application_queue
from tests.conftest import make_job_payload


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


def analysis_payload(score: int, verdict: str) -> dict:
    return {
        "overall_score": score,
        "verdict": verdict,
        "role_fit_score": score,
        "skill_fit_score": score,
        "experience_fit_score": score,
        "location_fit_score": 100,
        "salary_fit_score": 70,
        "matched_skills": ["AWS", "Terraform", "Kubernetes", "Python"],
        "missing_skills": ["Go"],
        "strengths": ["云运维经验匹配"],
        "gaps": [],
        "risk_flags": [],
        "experience_gap": "无明显差距",
        "role_summary": "云平台运维",
        "reasoning_summary": f"{verdict} 理由摘要。",
        "greeting_message": "您好，我有云运维经验，期待沟通。",
    }


@pytest.fixture
def make_analyzed(client, db, active_resume):
    """Create a job with a stored analysis, without calling any model."""
    counter = {"n": 0}

    def _make(score: int = 85, verdict: str = "apply", **job_overrides) -> int:
        counter["n"] += 1
        n = counter["n"]
        payload = make_job_payload(
            company=job_overrides.pop("company", f"公司{n}"),
            **job_overrides,
        )
        payload["raw_description"] = payload["raw_description"] + f"\n\n编号 {n}"
        job_id = client.post("/api/jobs", json=payload).json()["job"]["id"]

        db.add(
            JobAnalysis(
                job_id=job_id,
                resume_id=active_resume.id,
                model="test-model-fast",
                prompt_version="v1",
                cache_key=f"cache-{n}",
                overall_score=score,
                verdict=Verdict(verdict),
                result_json=analysis_payload(score, verdict),
            )
        )
        db.commit()
        return job_id

    return _make


def queue(client, **params) -> dict:
    return client.get("/api/application-queue", params=params or None).json()


def queue_ids(client, **params) -> list[int]:
    return [item["job_id"] for item in queue(client, **params)["items"]]


# --------------------------------------------------------------------------
# eligibility
# --------------------------------------------------------------------------


def test_unanalyzed_jobs_are_not_proposed(client):
    client.post("/api/jobs", json=make_job_payload())
    body = queue(client)
    assert body["total"] == 0
    assert body["summary"]["pending"] == 0


def test_strong_apply_and_apply_are_eligible(client, make_analyzed):
    strong = make_analyzed(92, "strong_apply")
    normal = make_analyzed(80, "apply")
    assert set(queue_ids(client)) == {strong, normal}


def test_maybe_is_excluded_by_default(client, make_analyzed):
    apply_id = make_analyzed(80, "apply")
    maybe_id = make_analyzed(64, "maybe")

    assert queue_ids(client) == [apply_id]
    assert set(queue_ids(client, include_maybe=True)) == {apply_id, maybe_id}


def test_queue_exposes_the_posting_url_without_its_query_string(client, make_analyzed, db):
    """The 去投递 action needs the job's own page; intake already stripped the query.

    Opening a page is not a decision: it must leave `Job.status` alone, exactly
    like copying the greeting does.
    """
    from app.models import Job

    job_id = make_analyzed(
        source_url="https://www.zhipin.com/job_detail/abc123.html?lid=SECRET&securityId=TOKEN",
    )
    db.get(Job, job_id).external_id = "abc123"
    db.commit()
    item = next(
        i for i in client.get("/api/application-queue").json()["items"] if i["job_id"] == job_id
    )
    assert item["source_url"] == "https://www.zhipin.com/job_detail/abc123.html"
    assert "lid" not in item["source_url"] and "securityId" not in item["source_url"]
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "new"


def test_a_job_without_a_url_reports_none_rather_than_a_broken_link(client, make_analyzed):
    job_id = make_analyzed(source_url=None)
    item = next(
        i for i in client.get("/api/application-queue").json()["items"] if i["job_id"] == job_id
    )
    assert item["source_url"] is None


def test_a_boss_url_not_backed_by_the_row_id_is_not_offered_as_a_link(client, make_analyzed):
    """The live-verification leftover `/job_detail/live1.html` sat first in the
    real queue with no `external_id`; clicking it opened a page that does not
    exist. A stored URL is not automatically a working link.

    The site is read from the URL's host, so a manually pasted BOSS link gets
    the same check even though it is stored with ``source="manual"``."""
    job_id = make_analyzed(source_url="https://www.zhipin.com/job_detail/live1.html")
    item = next(
        i for i in client.get("/api/application-queue").json()["items"] if i["job_id"] == job_id
    )
    assert item["source_url"] is None


def test_a_boss_url_matching_its_own_id_is_offered(client, make_analyzed, db):
    from app.models import Job

    job_id = make_analyzed(source_url="https://www.zhipin.com/job_detail/abc123XYZ.html")
    job = db.get(Job, job_id)
    job.external_id = "abc123XYZ"
    db.commit()

    item = next(
        i for i in client.get("/api/application-queue").json()["items"] if i["job_id"] == job_id
    )
    assert item["source_url"] == "https://www.zhipin.com/job_detail/abc123XYZ.html"


def test_the_city_menu_still_lists_every_city_while_one_is_selected(client, make_analyzed):
    """Picking 北京 must not delete 杭州 from the dropdown - the facet is counted
    with its own dimension excluded, so the user can switch without resetting."""
    make_analyzed(city="北京")
    make_analyzed(city="杭州")
    make_analyzed(city="上海")

    unfiltered = client.get("/api/application-queue").json()
    assert set(unfiltered["facets"]["cities"]) >= {"北京", "杭州", "上海"}

    filtered = client.get("/api/application-queue", params={"city": "北京"}).json()
    assert [i["city"] for i in filtered["items"]] == ["北京"], "the rows are still filtered"
    assert set(filtered["facets"]["cities"]) >= {"北京", "杭州", "上海"}


def test_skip_verdict_never_enters_the_queue(client, make_analyzed):
    make_analyzed(41, "skip")
    assert queue(client)["total"] == 0
    assert queue(client, include_maybe=True)["total"] == 0


def test_low_score_with_apply_verdict_stays_in_the_queue(client, make_analyzed):
    """Verdict is the primary signal - a modest score is not a silent filter."""
    job_id = make_analyzed(62, "apply")
    assert queue_ids(client) == [job_id]


def test_decided_jobs_leave_the_queue(client, make_analyzed):
    job_id = make_analyzed(88, "apply")
    assert queue_ids(client) == [job_id]

    client.post(f"/api/jobs/{job_id}/mark-applied", json={"confirmed": True})
    assert queue_ids(client) == []
    assert queue(client)["summary"]["pending"] == 0


@pytest.mark.parametrize("endpoint", ["skip", "mark-applied"])
def test_every_decision_removes_the_job_from_pending(client, make_analyzed, endpoint):
    job_id = make_analyzed(88, "apply")
    payload = {"confirmed": True} if endpoint == "mark-applied" else {}
    client.post(f"/api/jobs/{job_id}/{endpoint}", json=payload)
    assert queue_ids(client) == []


def test_reset_returns_a_job_to_the_queue(client, make_analyzed):
    job_id = make_analyzed(88, "apply")
    client.post(f"/api/jobs/{job_id}/skip", json={"reason": "其他"})
    assert queue_ids(client) == []

    client.post(f"/api/jobs/{job_id}/reset-status", json={})
    assert queue_ids(client) == [job_id]


# --------------------------------------------------------------------------
# the AI / human boundary
# --------------------------------------------------------------------------


def test_ai_verdict_never_changes_human_status(client, make_analyzed, db):
    """A `skip` verdict is advice; the job stays untouched until a human acts."""
    job_id = make_analyzed(41, "skip")

    db.expire_all()
    assert db.get(Job, job_id).status is JobStatus.new

    detail = client.get(f"/api/jobs/{job_id}").json()
    assert detail["status"] == "new"
    assert detail["latest_analysis"]["verdict"] == "skip"


def test_human_status_and_verdict_are_reported_separately(client, make_analyzed):
    job_id = make_analyzed(90, "strong_apply")
    client.post(f"/api/jobs/{job_id}/skip", json={"reason": "地点不合适"})

    item = queue(client, include_decided=True)["items"][0]
    assert item["verdict"] == "strong_apply", "the recommendation is unchanged"
    assert item["job_status"] == "skipped", "the human decided otherwise"
    assert item["proposal_state"] == "dismissed"


# --------------------------------------------------------------------------
# later
# --------------------------------------------------------------------------


def test_deferred_jobs_drop_out_of_pending_but_stay_eligible(client, make_analyzed):
    job_id = make_analyzed(88, "apply")
    client.post(f"/api/jobs/{job_id}/later", json={"preset": "tomorrow"})

    body = queue(client)
    assert body["summary"]["pending"] == 0
    assert body["summary"]["later"] == 1
    assert body["items"][0]["proposal_state"] == "later"


def test_a_due_deferral_returns_to_the_queue(client, make_analyzed, db):
    job_id = make_analyzed(88, "apply")
    client.post(f"/api/jobs/{job_id}/later", json={"preset": "tomorrow"})

    # Time passes: the deferral is now in the past.
    db.expire_all()
    job = db.get(Job, job_id)
    job.review_after = datetime.now(timezone.utc) - timedelta(hours=1)
    db.commit()

    body = queue(client)
    assert body["summary"]["pending"] == 1
    assert body["items"][0]["proposal_state"] in ("pending", "ready")


def test_deferred_jobs_sort_after_due_ones(client, make_analyzed, db):
    deferred = make_analyzed(95, "strong_apply")
    due = make_analyzed(70, "apply")
    client.post(f"/api/jobs/{deferred}/later", json={"preset": "tomorrow"})

    assert queue_ids(client) == [due, deferred]


# --------------------------------------------------------------------------
# sorting
# --------------------------------------------------------------------------


def test_default_sort_puts_strong_apply_first(client, make_analyzed):
    lower_apply = make_analyzed(88, "apply")
    strong = make_analyzed(80, "strong_apply")

    assert queue_ids(client) == [strong, lower_apply], "verdict outranks raw score"


def test_sort_by_score(client, make_analyzed):
    low = make_analyzed(70, "strong_apply")
    high = make_analyzed(95, "apply")
    assert queue_ids(client, sort="score") == [high, low]


def test_sort_by_newest(client, make_analyzed):
    first = make_analyzed(90, "apply")
    second = make_analyzed(60, "apply")
    assert queue_ids(client, sort="newest")[0] == second
    assert first in queue_ids(client, sort="newest")


def test_sort_by_salary_puts_unparseable_last(client, make_analyzed):
    """Salaries we cannot parse are not given an invented value."""
    rich = make_analyzed(70, "apply", salary_text="40-60K")
    modest = make_analyzed(70, "apply", salary_text="15-20K")
    unknown = make_analyzed(70, "apply", salary_text="面议")

    order = queue_ids(client, sort="salary")
    assert order.index(rich) < order.index(modest) < order.index(unknown)


# --------------------------------------------------------------------------
# filters
# --------------------------------------------------------------------------


def test_filter_by_city(client, make_analyzed):
    beijing = make_analyzed(80, "apply", city="北京")
    make_analyzed(80, "apply", city="上海")
    assert queue_ids(client, city="北京") == [beijing]


def test_filter_by_verdict(client, make_analyzed):
    strong = make_analyzed(92, "strong_apply")
    make_analyzed(80, "apply")
    assert queue_ids(client, verdict="strong_apply") == [strong]


def test_filter_by_min_score(client, make_analyzed):
    high = make_analyzed(90, "apply")
    make_analyzed(65, "apply")
    assert queue_ids(client, min_score=80) == [high]


def test_filter_by_keyword(client, make_analyzed):
    target = make_analyzed(80, "apply", title="SRE 工程师")
    make_analyzed(80, "apply", title="数据分析师")
    assert queue_ids(client, keyword="SRE") == [target]


def test_filter_by_source(client, make_analyzed):
    boss_job = make_analyzed(80, "apply", source="boss", external_id="b1")
    make_analyzed(80, "apply")
    assert queue_ids(client, source="boss") == [boss_job]


def test_facets_expose_cities_and_sources(client, make_analyzed):
    make_analyzed(80, "apply", city="北京")
    make_analyzed(80, "apply", city="上海")

    facets = queue(client)["facets"]
    assert facets["cities"]["北京"] == 1
    assert facets["cities"]["上海"] == 1
    assert "manual" in facets["sources"]


# --------------------------------------------------------------------------
# summary
# --------------------------------------------------------------------------


def test_summary_counts(client, make_analyzed):
    make_analyzed(95, "strong_apply")
    make_analyzed(85, "apply")
    make_analyzed(82, "apply")
    deferred = make_analyzed(80, "apply")
    client.post(f"/api/jobs/{deferred}/later", json={"preset": "tomorrow"})

    summary = queue(client)["summary"]
    assert summary["pending"] == 3
    assert summary["strong_apply"] == 1
    assert summary["apply"] == 2
    assert summary["later"] == 1
    assert summary["daily_target"] == 10
    assert summary["timezone"] == "Asia/Tokyo"


def test_applied_today_counts_todays_events(client, make_analyzed):
    a = make_analyzed(90, "apply")
    b = make_analyzed(88, "apply")
    client.post(f"/api/jobs/{a}/mark-applied", json={"confirmed": True})
    client.post(f"/api/jobs/{b}/mark-applied", json={"confirmed": True})

    summary = queue(client)["summary"]
    assert summary["applied_today"] == 2
    assert summary["pending"] == 0


def test_today_counters_cover_every_daily_metric(client, make_analyzed):
    applied = make_analyzed(90, "apply")
    skipped = make_analyzed(88, "apply")

    client.post(f"/api/jobs/{applied}/mark-applied", json={"confirmed": True})
    client.post(f"/api/jobs/{applied}/reply", json={"response_type": "positive"})
    client.post(f"/api/jobs/{applied}/interview", json={"round": "一面"})
    client.post(f"/api/jobs/{skipped}/skip", json={"reason": "薪资太低"})

    summary = queue(client)["summary"]
    assert summary["applied_today"] == 1
    assert summary["replied_today"] == 1
    assert summary["interview_today"] == 1
    assert summary["skipped_today"] == 1


def test_proposal_carries_the_analysis_payload(client, make_analyzed):
    make_analyzed(91, "strong_apply")
    item = queue(client)["items"][0]

    assert item["overall_score"] == 91
    assert item["matched_skills"][:2] == ["AWS", "Terraform"]
    assert item["missing_skills"] == ["Go"]
    assert item["greeting_message"]
    assert item["reasoning_summary"]
    assert item["analyzed_at"]


def test_duplicate_jobs_do_not_duplicate_proposals(client, make_analyzed):
    """Dedup happens at intake, so one job means one proposal."""
    job_id = make_analyzed(88, "apply", company="重复公司")
    stored = client.get(f"/api/jobs/{job_id}").json()

    duplicate = client.post(
        "/api/jobs",
        json=make_job_payload(
            company=stored["company"],
            title=stored["title"],
            raw_description=stored["raw_description"],
        ),
    )
    assert duplicate.status_code == 409
    assert queue(client)["total"] == 1


def test_reanalysis_updates_the_proposal_without_a_second_row(client, make_analyzed, db, active_resume):
    job_id = make_analyzed(70, "apply")
    db.add(
        JobAnalysis(
            job_id=job_id,
            resume_id=active_resume.id,
            model="test-model-smart",
            prompt_version="v1",
            cache_key="cache-reanalysis",
            overall_score=93,
            verdict=Verdict.strong_apply,
            result_json=analysis_payload(93, "strong_apply"),
        )
    )
    db.commit()

    body = queue(client)
    assert body["total"] == 1, "the queue is derived, not accumulated"
    assert body["items"][0]["overall_score"] == 93
    assert body["items"][0]["verdict"] == "strong_apply"
