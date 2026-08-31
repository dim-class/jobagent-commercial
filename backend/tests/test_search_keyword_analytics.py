"""Which search keyword surfaced what - deterministic, zero AI.

The honesty rules here are the same ones v0.6 analytics live by: a rate never
appears without its counts, a thin cohort is named rather than silently sorted
away, and nothing claims a keyword *causes* replies.
"""

from __future__ import annotations

import hashlib

from app.models import Job, JobAnalysis, JobSearchTask, JobStatus, TaskCandidate, Verdict
from app.services import search_keyword_analytics as ska
from app.services.statistics import Confidence


def make_job(db, n: int) -> Job:
    job = Job(
        source="boss",
        external_id=f"kw{n}",
        source_url=f"https://www.zhipin.com/job_detail/kw{n}.html",
        company=f"公司{n}",
        title=f"岗位{n}",
        city="北京",
        raw_description=f"JD {n}",
        normalized_description=f"jd {n}",
        content_hash=hashlib.sha256(f"kw-{n}".encode()).hexdigest(),
        status=JobStatus.new,
    )
    db.add(job)
    db.flush()
    return job


def analyzed(db, resume, n: int, *, score: int, verdict: Verdict) -> Job:
    job = make_job(db, n)
    db.add(
        JobAnalysis(
            job_id=job.id,
            resume_id=resume.id,
            model="test-model-fast",
            prompt_version="v1",
            cache_key=f"key-{n}",
            overall_score=score,
            verdict=verdict,
            result_json={"overall_score": score},
        )
    )
    return job


def task_with(db, keyword: str, jobs: list[Job], *, city: str = "北京") -> JobSearchTask:
    task = JobSearchTask(name=f"{city} · {keyword}", keywords=keyword, city=city)
    db.add(task)
    db.flush()
    for job in jobs:
        db.add(TaskCandidate(task_id=task.id, job_id=job.id))
    db.commit()
    return task


def test_a_keyword_reports_its_average_and_recommend_rate_with_counts(db, active_resume):
    jobs = [
        analyzed(db, active_resume, i, score=80, verdict=Verdict.apply) for i in range(3)
    ] + [
        analyzed(db, active_resume, 10 + i, score=40, verdict=Verdict.skip) for i in range(2)
    ]
    task_with(db, "云计算工程师", jobs)

    result = ska.compute(db)
    cohort = next(c for c in result.cohorts if c.keyword == "云计算工程师")
    assert cohort.jobs == 5
    assert cohort.recommended == 3
    assert cohort.recommend_rate == 0.6
    assert cohort.average_score == 64.0
    assert cohort.interval is not None, "a rate never appears without its interval"


def test_a_keyword_that_surfaced_nothing_worth_applying_to_is_called_out(db, active_resume):
    """The actionable finding: where the analysis budget went with no return."""
    jobs = [
        analyzed(db, active_resume, i, score=30, verdict=Verdict.skip) for i in range(8)
    ]
    task_with(db, "基础设施", jobs)

    result = ska.compute(db)
    cohort = next(c for c in result.cohorts if c.keyword == "基础设施")
    assert cohort.recommended == 0
    assert cohort.recommend_rate == 0.0
    assert cohort.actionable is True
    assert any("没有一个被判定为推荐投递" in line for line in result.observations)


def test_a_lucky_tiny_cohort_never_outranks_a_solid_one(db, active_resume):
    """2/2 scores a higher raw rate than 8/28 but must not be presented first."""
    tiny = [analyzed(db, active_resume, i, score=90, verdict=Verdict.apply) for i in range(2)]
    task_with(db, "AWS", tiny)

    solid = [
        analyzed(db, active_resume, 100 + i, score=70, verdict=Verdict.apply)
        for i in range(8)
    ] + [
        analyzed(db, active_resume, 200 + i, score=50, verdict=Verdict.skip)
        for i in range(12)
    ]
    task_with(db, "云计算工程师", solid)

    result = ska.compute(db)
    assert result.cohorts[0].keyword == "云计算工程师"
    aws = next(c for c in result.cohorts if c.keyword == "AWS")
    assert aws.recommend_rate == 1.0, "its raw rate really is higher"
    assert aws.confidence is Confidence.insufficient
    assert aws.actionable is False


def test_a_thin_cohort_is_named_rather_than_silently_dropped(db, active_resume):
    task_with(db, "SRE", [analyzed(db, active_resume, 1, score=60, verdict=Verdict.maybe)])
    big = [
        analyzed(db, active_resume, 100 + i, score=70, verdict=Verdict.apply)
        for i in range(10)
    ]
    task_with(db, "云计算工程师", big)

    result = ska.compute(db)
    assert any("SRE" in line and "样本不足" in line for line in result.observations)


def test_jobs_collected_outside_a_search_task_are_reported_not_blamed(db, active_resume):
    task_with(db, "云计算工程师", [analyzed(db, active_resume, 1, score=70, verdict=Verdict.apply)])
    analyzed(db, active_resume, 2, score=50, verdict=Verdict.skip)  # manual paste
    db.commit()

    result = ska.compute(db)
    assert result.analyzed_jobs == 2
    assert result.attributed_jobs == 1
    assert result.unattributed_jobs == 1
    assert result.coverage == 0.5
    assert sum(c.jobs for c in result.cohorts) == 1, "unattributed never joins a keyword"


def test_one_job_found_by_two_tasks_with_the_same_keyword_counts_once(db, active_resume):
    job = analyzed(db, active_resume, 1, score=70, verdict=Verdict.apply)
    task_with(db, "云计算工程师", [job], city="北京")
    task_with(db, "云计算工程师", [job], city="上海")

    result = ska.compute(db)
    cohort = next(c for c in result.cohorts if c.keyword == "云计算工程师")
    assert cohort.jobs == 1
    assert set(cohort.cities) == {"北京", "上海"}


def test_an_unanalyzed_job_has_no_score_to_average(db, active_resume):
    scored = analyzed(db, active_resume, 1, score=70, verdict=Verdict.apply)
    unscored = make_job(db, 2)
    task_with(db, "云计算工程师", [scored, unscored])

    result = ska.compute(db)
    cohort = next(c for c in result.cohorts if c.keyword == "云计算工程师")
    assert cohort.jobs == 1, "a job with no analysis cannot be averaged"


def test_an_empty_database_says_so_instead_of_ranking_nothing(db):
    result = ska.compute(db)
    assert result.cohorts == []
    assert result.coverage is None
    assert any("样本还不足" in line for line in result.observations)


def test_reading_the_analytics_never_calls_a_model(db, active_resume, monkeypatch):
    def _explode(**kwargs):
        raise AssertionError("analytics must never call the model")

    monkeypatch.setattr("app.services.job_matcher.run_job_match", _explode)
    task_with(db, "云计算工程师", [analyzed(db, active_resume, 1, score=70, verdict=Verdict.apply)])
    ska.compute(db)


def test_the_route_returns_the_same_numbers(client, db, active_resume):
    jobs = [analyzed(db, active_resume, i, score=80, verdict=Verdict.apply) for i in range(6)]
    task_with(db, "云计算工程师", jobs)

    body = client.get("/api/analytics/search-keywords").json()
    cohort = next(c for c in body["cohorts"] if c["keyword"] == "云计算工程师")
    assert cohort["jobs"] == 6
    assert cohort["recommended"] == 6
    assert cohort["recommend_rate"] == 1.0
    assert cohort["interval_low"] is not None and cohort["interval_high"] is not None
    assert body["attributed_jobs"] == 6
    assert any("不代表这些岗位更容易收到回复" in line for line in body["observations"])
