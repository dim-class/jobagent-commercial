"""Direction choice follows the résumé and past results - never a model call.

The old behaviour took `preferred_roles` in configured order, which is why an
English direction BOSS barely matches could outrank a Chinese one the user is
qualified for, and why a direction proven to surface nothing kept being run.
"""

from __future__ import annotations

import hashlib

import pytest

from app.models import Job, JobAnalysis, JobSearchTask, JobStatus, TaskCandidate, Verdict
from app.schemas.direction import DirectionFit, ResumeDirectionAnalysis
from app.services import search_direction_ranking as sdr

STRATEGY = {
    "preferred_roles": ["Cloud Engineer", "云计算工程师", "基础设施", "云平台工程师"],
    "relevant_skills": ["AWS", "Terraform"],
}


class FakeResume:
    def __init__(self, profile: dict) -> None:
        self.parsed_profile_json = profile


CLOUD_RESUME = FakeResume(
    {
        "skills": ["AWS", "Linux", "Terraform"],
        # The narrative is where a Chinese résumé actually names its role.
        "work_experience": ["基础设施工程师，负责云平台运维与云迁移项目"],
    }
)


def searched(
    db, resume, keyword: str, *, total: int, recommended: int, skipped: int = 0
) -> None:
    """Give one direction a real history of surfaced jobs.

    The first `recommended` jobs carry an apply verdict, and the first `skipped`
    of those the user then turned down. Every other job is still undecided.
    """
    assert skipped <= recommended
    task = JobSearchTask(name=f"北京 · {keyword}", keywords=keyword, city="北京")
    db.add(task)
    db.flush()
    for i in range(total):
        job = Job(
            source="boss",
            external_id=f"{keyword}-{i}",
            source_url=f"https://www.zhipin.com/job_detail/{abs(hash(keyword))}{i}.html",
            company=f"公司{i}",
            title=f"岗位{i}",
            raw_description="JD",
            normalized_description="jd",
            content_hash=hashlib.sha256(f"{keyword}-{i}".encode()).hexdigest(),
            status=JobStatus.skipped if i < skipped else JobStatus.new,
        )
        db.add(job)
        db.flush()
        db.add(
            JobAnalysis(
                job_id=job.id,
                resume_id=resume.id,
                model="test-model-fast",
                prompt_version="v1",
                cache_key=f"{keyword}-{i}",
                overall_score=70 if i < recommended else 40,
                verdict=Verdict.apply if i < recommended else Verdict.skip,
                result_json={},
            )
        )
        db.add(TaskCandidate(task_id=task.id, job_id=job.id))
    db.commit()


def ai_fits(**fits: int) -> ResumeDirectionAnalysis:
    return ResumeDirectionAnalysis(
        directions=[
            DirectionFit(keyword=k, fit=v, reason="简历里写过相关经历") for k, v in fits.items()
        ],
        summary="偏云与基础设施方向",
    )


def test_a_direction_matching_the_resume_outranks_one_that_does_not(db):
    ranking = sdr.rank(db, resume=CLOUD_RESUME, strategy=STRATEGY)
    order = [d.keyword for d in ranking.directions]

    assert order.index("云计算工程师") < order.index("Cloud Engineer")
    top = ranking.directions[0]
    assert top.fit > 0, "the top choice must actually overlap the résumé"
    assert any("重合度" in reason for reason in top.reasons)


def test_a_direction_that_surfaced_nothing_is_pushed_to_the_end(db, active_resume):
    """基础设施 really did surface 8 jobs with 0 recommendations."""
    searched(db, active_resume, "基础设施", total=8, recommended=0)

    ranking = sdr.rank(db, resume=CLOUD_RESUME, strategy=STRATEGY)
    order = [d.keyword for d in ranking.directions]
    empty = next(d for d in ranking.directions if d.keyword == "基础设施")

    assert order[-1] == "基础设施" or order.index("基础设施") > order.index("云计算工程师")
    assert empty.useful == 0
    assert any("没有一个" in reason for reason in empty.reasons)
    assert any("没有一个值得投递" in note for note in ranking.notes)


def test_with_enough_history_the_record_decides_not_the_fit(db, active_resume):
    """A direction that reads like the résumé but keeps surfacing nothing worth
    applying to must rank below one that reads less like it and does.

    Chosen so the old ``fit + recommend_rate * 2`` fails it: 基础设施 overlaps
    the résumé at 75% against 50%, and a 13% record against a 3% one moved the
    old score by only 0.2.
    """
    searched(db, active_resume, "基础设施", total=30, recommended=1)
    searched(db, active_resume, "云平台工程师", total=30, recommended=4)

    ranking = sdr.rank(db, resume=CLOUD_RESUME, strategy=STRATEGY)
    by_keyword = {d.keyword: d for d in ranking.directions}
    assert by_keyword["基础设施"].fit > by_keyword["云平台工程师"].fit, "fit says the opposite"

    order = [d.keyword for d in ranking.directions]
    assert order.index("云平台工程师") < order.index("基础设施")
    assert any("值得投递" in reason for reason in by_keyword["云平台工程师"].reasons)


def test_the_models_noise_cannot_outvote_a_deep_record(db, active_resume):
    """The 2026-09-11 ranking, scaled down.

    The model judged 基础设施工程师 85 and 云运维工程师 58 - and the same model
    read 云运维工程师 as 69, 80 and 58 on the same résumé - while the searches
    said one useful posting in 22 against 16%. The old score put the
    better-sounding direction first, and a four-city run never searched the
    better one.
    """
    strategy = {"preferred_roles": ["基础设施工程师", "云运维工程师"], "relevant_skills": []}
    searched(db, active_resume, "基础设施工程师", total=22, recommended=1)
    searched(db, active_resume, "云运维工程师", total=50, recommended=8)

    ranking = sdr.rank(
        db, resume=active_resume, strategy=strategy, ai=ai_fits(基础设施工程师=85, 云运维工程师=58)
    )
    assert [d.keyword for d in ranking.directions] == ["云运维工程师", "基础设施工程师"]


def test_a_recommendation_the_user_turned_down_is_not_a_useful_result(db, active_resume):
    """What the user did outranks what the model thought.

    On real data the recommend rate alone ranked 中间件工程师 - a quarter of
    whose recommendations the user skipped - above WebSphere工程师, whose 25
    recommendations were applied to 24 times.
    """
    strategy = {"preferred_roles": ["云计算工程师", "云平台工程师"], "relevant_skills": []}
    searched(db, active_resume, "云计算工程师", total=20, recommended=8, skipped=6)
    searched(db, active_resume, "云平台工程师", total=20, recommended=4)

    ranking = sdr.rank(
        db, resume=active_resume, strategy=strategy, ai=ai_fits(云计算工程师=70, 云平台工程师=70)
    )
    by_keyword = {d.keyword: d for d in ranking.directions}
    assert by_keyword["云计算工程师"].recommended > by_keyword["云平台工程师"].recommended
    assert (by_keyword["云计算工程师"].useful, by_keyword["云平台工程师"].useful) == (2, 4)
    assert ranking.directions[0].keyword == "云平台工程师"


def test_a_thin_history_is_not_treated_as_evidence(db, active_resume):
    searched(db, active_resume, "云计算工程师", total=2, recommended=2)

    ranking = sdr.rank(db, resume=CLOUD_RESUME, strategy=STRATEGY)
    thin = next(d for d in ranking.directions if d.keyword == "云计算工程师")
    assert thin.has_evidence is False, "2/2 is not evidence"
    assert ranking.needs_more_evidence is True
    assert any("样本" in note for note in ranking.notes)


def test_ranking_is_stable_for_directions_that_tie(db):
    """Ties fall back to configured order, so the result never shuffles."""
    first = [d.keyword for d in sdr.rank(db, resume=None, strategy=STRATEGY).directions]
    second = [d.keyword for d in sdr.rank(db, resume=None, strategy=STRATEGY).directions]
    assert first == second


def test_an_empty_strategy_says_so_rather_than_inventing_directions(db):
    ranking = sdr.rank(db, resume=CLOUD_RESUME, strategy={"preferred_roles": []})
    assert ranking.directions == []
    assert ranking.needs_more_evidence is True
    assert any("没有岗位方向" in note for note in ranking.notes)


def test_ranking_never_calls_a_model(db, active_resume, monkeypatch):
    def _explode(**kwargs):
        raise AssertionError("direction ranking must never call the model")

    monkeypatch.setattr("app.services.job_matcher.run_job_match", _explode)
    searched(db, active_resume, "云计算工程师", total=10, recommended=3)
    sdr.rank(db, resume=CLOUD_RESUME, strategy=STRATEGY)


def test_the_comprehensive_search_uses_the_ranking_and_reports_why(
    client, db, active_resume
):
    """The user asked to search directly and be told afterwards which
    directions were used, so the response carries the reasons."""
    searched(db, active_resume, "基础设施", total=8, recommended=0)

    body = client.post(
        "/api/tasks/search-plan/quick-prepare",
        json={"cities": ["北京"], "target_count": 3},
    ).json()

    used = [d["keyword"] for d in body["directions"]]
    assert used, "the response must say which directions it used"
    assert set(used) == {task["keywords"] for task in body["tasks"]}
    assert "基础设施" not in used or used[-1] == "基础设施"
    assert body["direction_notes"], "and why"
    # The console's 历史 column renders the count the order was decided on.
    assert all("useful" in d and "useful_rate" in d for d in body["directions"])


# ---------------------------------------------------------------------------
# A keyword BOSS returns nothing for
# ---------------------------------------------------------------------------


def _completed_search(db, keyword: str, observed: int):
    from app.models import JobSearchTask
    from app.models.enums import SearchTaskRunStatus, TaskMode

    db.add(
        JobSearchTask(
            name=f"北京 · {keyword}",
            keywords=keyword,
            city="北京",
            is_search_plan=True,
            mode=TaskMode.manual_review_only,
            run_status=SearchTaskRunStatus.completed,
            observed_count=observed,
        )
    )
    db.commit()


def test_a_keyword_that_never_rendered_a_card_is_penalised(db, active_resume):
    """Five of sixteen search units spent finding nothing, twice over.

    This is not "these jobs are a poor match" - that is a judgement the ranking
    already makes. It is "BOSS returns no results for this string", which the
    search itself established, and repeating it costs a whole unit.

    Asserted as a score *delta* for the same keyword: comparing two different
    keywords would pass on the Chinese bonus alone and prove nothing about the
    penalty.
    """
    strategy = {"preferred_roles": ["DevOps Engineer"], "must_have_skills": []}

    before = sdr.rank(db, resume=active_resume, strategy=strategy).directions[0]
    for _ in range(2):
        _completed_search(db, "DevOps Engineer", 0)
    after = sdr.rank(db, resume=active_resume, strategy=strategy).directions[0]

    # A real drop, not "score minus the constant", which would hold even if the
    # constant were 0 and the penalty therefore did nothing.
    assert after.score < before.score
    assert after.score == pytest.approx(before.score - sdr._BARREN_PENALTY)
    assert any("一张卡片都没返回" in reason for reason in after.reasons)
    assert not any("一张卡片都没返回" in reason for reason in before.reasons)


def test_a_barren_keyword_ends_up_behind_one_that_returns_cards(db, active_resume):
    strategy = {"preferred_roles": ["云计算工程师", "DevOps Engineer"], "must_have_skills": []}
    for _ in range(2):
        _completed_search(db, "DevOps Engineer", 0)
        _completed_search(db, "云计算工程师", 30)

    keywords = [d.keyword for d in sdr.rank(db, resume=active_resume, strategy=strategy).directions]
    assert keywords.index("云计算工程师") < keywords.index("DevOps Engineer")


def test_one_barren_run_is_not_enough_to_condemn_a_keyword(db, active_resume):
    """A single run can end early for its own reasons - a lost tab, a pause."""
    strategy = {"preferred_roles": ["云计算工程师", "DevOps Engineer"], "must_have_skills": []}
    _completed_search(db, "DevOps Engineer", 0)

    ranking = sdr.rank(db, resume=active_resume, strategy=strategy)
    barren = next(d for d in ranking.directions if d.keyword == "DevOps Engineer")
    assert not any("一张卡片都没返回" in reason for reason in barren.reasons)


def test_a_barren_keyword_is_dropped_from_the_plan_not_just_ranked_last(db, active_resume):
    """Four dead keywords are a quarter of a sixteen-unit run.

    Demotion alone was not enough: the candidate list is short enough that a
    demoted keyword still gets picked, and a unit spent on a keyword BOSS
    returns nothing for is a unit that collects nothing.
    """
    strategy = {
        "preferred_roles": ["云计算工程师", "DevOps Engineer", "云平台工程师"],
        "must_have_skills": [],
    }
    for _ in range(2):
        _completed_search(db, "DevOps Engineer", 0)

    ranking = sdr.rank(db, resume=active_resume, strategy=strategy)
    assert "DevOps Engineer" in [d.keyword for d in ranking.directions], "still reported"
    assert next(d for d in ranking.directions if d.keyword == "DevOps Engineer").barren
    # ...but never handed to the planner.
    assert "DevOps Engineer" not in ranking.top(10)
    assert set(ranking.top(10)) == {"云计算工程师", "云平台工程师"}


def test_dropping_never_leaves_the_planner_with_nothing(db, active_resume):
    """If every direction is barren, an ordered plan still beats no plan."""
    strategy = {"preferred_roles": ["DevOps Engineer"], "must_have_skills": []}
    for _ in range(2):
        _completed_search(db, "DevOps Engineer", 0)

    assert sdr.rank(db, resume=active_resume, strategy=strategy).top(10) == ["DevOps Engineer"]
