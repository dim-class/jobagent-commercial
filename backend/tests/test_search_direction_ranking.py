"""Direction choice follows the résumé and past results - never a model call.

The old behaviour took `preferred_roles` in configured order, which is why an
English direction BOSS barely matches could outrank a Chinese one the user is
qualified for, and why a direction proven to surface nothing kept being run.
"""

from __future__ import annotations

import hashlib

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
