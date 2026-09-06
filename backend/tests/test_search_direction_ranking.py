"""Direction choice follows the résumé and past results - never a model call.

The old behaviour took `preferred_roles` in configured order, which is why an
English direction BOSS barely matches could outrank a Chinese one the user is
qualified for, and why a direction proven to surface nothing kept being run.
"""

from __future__ import annotations

import hashlib

import pytest

from app.models import Job, JobAnalysis, JobSearchTask, JobStatus, TaskCandidate, Verdict
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


def searched(db, resume, keyword: str, *, total: int, recommended: int) -> None:
    """Give one direction a real history of surfaced jobs."""
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
            status=JobStatus.new,
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
    assert empty.recommended == 0
    assert any("没有一个" in reason or "推荐率" in reason for reason in empty.reasons)
    assert any("无一推荐" in note for note in ranking.notes)


def test_real_history_outweighs_a_text_overlap_guess(db, active_resume):
    """A direction with a proven rate beats one that merely reads similar."""
    searched(db, active_resume, "云平台工程师", total=12, recommended=6)

    ranking = sdr.rank(db, resume=CLOUD_RESUME, strategy=STRATEGY)
    proven = next(d for d in ranking.directions if d.keyword == "云平台工程师")
    assert proven.has_evidence is True
    assert ranking.directions[0].keyword == "云平台工程师"
    assert any("历史推荐率" in reason for reason in proven.reasons)


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
