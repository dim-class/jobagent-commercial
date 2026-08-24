"""Deterministic pre-analysis helpers and post-analysis guardrails."""

from __future__ import annotations

import pytest

from app.core.career_strategy import load_strategy
from app.services.scoring import (
    apply_guardrails,
    compute_pre_analysis,
    extract_experience_requirement,
    extract_salary_range,
    match_city,
    match_skills,
    verdict_for_score,
)
from tests.conftest import HELPDESK_JD, SAMPLE_JD


@pytest.fixture
def strategy():
    return load_strategy(force=True)


# --------------------------------------------------------------------------
# experience extraction
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected_min", "expected_max"),
    [
        ("1-3年", 1, 3),
        ("3~5 年", 3, 5),
        ("要求 3 到 5 年相关经验", 3, 5),
        ("5年以上工作经验", 5, None),
        ("至少 2 年运维经验", 2, None),
        ("minimum 4 years of experience", 4, None),
    ],
)
def test_extract_experience_years(text, expected_min, expected_max):
    req = extract_experience_requirement(text)
    assert req.min_years == expected_min
    assert req.max_years == expected_max


def test_extract_experience_unlimited():
    assert extract_experience_requirement("经验不限，欢迎应届生").unlimited is True


def test_extract_experience_detects_hard_requirement():
    req = extract_experience_requirement("硬性要求：8 年以上云计算经验，不满足勿投")
    assert req.min_years == 8
    assert req.is_hard_requirement is True
    assert "硬性" in req.describe()


def test_extract_experience_missing_is_not_an_error():
    req = extract_experience_requirement("岗位职责：负责云平台运维")
    assert req.min_years is None
    assert req.describe() == "未明确"


# --------------------------------------------------------------------------
# salary / city / skills
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("20k-30k·13薪", (20000, 30000)),
        # the unit is usually written once, at the end
        ("40-60K", (40000, 60000)),
        ("20-30K·15薪", (20000, 30000)),
        ("12-18K/月", (12000, 18000)),
        ("15K~25K", (15000, 25000)),
        ("18000-26000", (18000, 26000)),
        ("面议", (None, None)),
        (None, (None, None)),
    ],
)
def test_extract_salary_range(text, expected):
    assert extract_salary_range(text) == expected


def test_match_city_uses_target_list(strategy):
    matched, reason = match_city("北京", "", strategy["target_cities"], True)
    assert matched is True and "北京" in reason

    matched, reason = match_city("成都", "", strategy["target_cities"], True)
    assert matched is False and "不在目标城市" in reason


def test_match_city_accepts_remote_when_allowed(strategy):
    matched, reason = match_city("成都", "本岗位支持远程办公", strategy["target_cities"], True)
    assert matched is True and "远程" in reason


def test_match_skills_resolves_aliases(strategy):
    matched, _ = match_skills("我们使用 K8s 与 Jenkins", ["Kubernetes", "CI/CD"], strategy["skill_aliases"])
    assert set(matched) == {"Kubernetes", "CI/CD"}


def test_short_acronyms_require_word_boundaries(strategy):
    """"WAS" must not match the English word "was"."""
    matched, missing = match_skills(
        "the system was designed for specs", ["WAS", "ECS"], strategy["skill_aliases"]
    )
    assert matched == []
    assert set(missing) == {"WAS", "ECS"}


# --------------------------------------------------------------------------
# the full feature block
# --------------------------------------------------------------------------


def _pre(strategy, **overrides):
    kwargs = {
        "title": "云计算工程师",
        "company": "示例科技",
        "city": "北京",
        "salary_text": "20k-30k",
        "experience_text": "1-3年",
        "normalized_description": SAMPLE_JD,
        "strategy": strategy,
        "resume_skills": ["AWS", "Linux", "Terraform", "Python"],
    }
    kwargs.update(overrides)
    return compute_pre_analysis(**kwargs)


def test_pre_analysis_on_a_good_match(strategy):
    pre = _pre(strategy)
    assert pre.city_match is True
    assert pre.title_family_match is True
    assert {"AWS", "Terraform", "Docker", "Kubernetes"} <= set(pre.matched_skills)
    assert pre.excluded_hits == []
    assert pre.experience_within_preference is True
    assert pre.salary_meets_minimum is True
    assert pre.heuristic_score >= 75


def test_pre_analysis_flags_excluded_role(strategy):
    pre = _pre(
        strategy,
        title="IT 桌面运维工程师（Helpdesk）",
        city="深圳",
        salary_text="8k-11k",
        normalized_description=HELPDESK_JD,
    )
    assert pre.excluded_hits, "helpdesk keywords should be detected"
    assert pre.excluded_softened is False
    assert pre.city_match is False
    assert pre.heuristic_score < 50


def test_pre_analysis_is_serialisable(strategy):
    data = _pre(strategy).to_dict()
    assert data["city"] == "北京"
    assert isinstance(data["matched_skills"], list)
    assert isinstance(data["heuristic_score"], int)


def test_heuristic_score_stays_in_range(strategy):
    for city in ("北京", "成都", None):
        for title in ("云计算工程师", "销售代表"):
            pre = _pre(strategy, city=city, title=title)
            assert 0 <= pre.heuristic_score <= 100


# --------------------------------------------------------------------------
# guardrails
# --------------------------------------------------------------------------


def _model_output(**overrides):
    payload = {
        "overall_score": 85,
        "verdict": "apply",
        "role_fit_score": 85,
        "skill_fit_score": 80,
        "experience_fit_score": 75,
        "location_fit_score": 100,
        "salary_fit_score": 80,
        "matched_skills": ["AWS"],
        "missing_skills": [],
        "strengths": ["云运维经验匹配"],
        "gaps": [],
        "risk_flags": [],
        "experience_gap": "无明显差距",
        "role_summary": "云平台运维",
        "reasoning_summary": "技能匹配良好",
        "greeting_message": "您好",
    }
    payload.update(overrides)
    return payload


def test_guardrails_clamp_out_of_range_scores(strategy):
    pre = _pre(strategy)
    out = apply_guardrails(_model_output(overall_score=140, role_fit_score=-20), pre, strategy)
    assert out["overall_score"] == 100
    assert out["role_fit_score"] == 0


def test_guardrails_cap_excluded_roles(strategy):
    pre = _pre(
        strategy,
        title="IT 桌面运维工程师（Helpdesk）",
        normalized_description=HELPDESK_JD,
        city="深圳",
    )
    cap = strategy["scoring"]["excluded_role_score_cap"]
    out = apply_guardrails(_model_output(overall_score=88, verdict="apply"), pre, strategy)
    assert out["overall_score"] == cap
    assert out["verdict"] == "skip"
    assert any("排除关键词" in flag for flag in out["risk_flags"])


def test_guardrails_keep_a_reasonable_model_verdict(strategy):
    """A 3-5 year job with a strong technical match must not be downgraded."""
    pre = _pre(strategy, experience_text="3-5年")
    out = apply_guardrails(_model_output(overall_score=82, verdict="apply"), pre, strategy)
    assert out["overall_score"] == 82
    assert out["verdict"] == "apply"
    assert out["risk_flags"] == []


def test_guardrails_correct_a_wildly_inconsistent_verdict(strategy):
    pre = _pre(strategy)
    out = apply_guardrails(_model_output(overall_score=92, verdict="skip"), pre, strategy)
    assert out["verdict"] == "strong_apply"
    assert any("不一致" in flag for flag in out["risk_flags"])


@pytest.mark.parametrize(
    ("score", "expected"),
    [(95, "strong_apply"), (85, "apply"), (72, "apply"), (64, "maybe"), (30, "skip")],
)
def test_verdict_bands(strategy, score, expected):
    assert verdict_for_score(score, strategy) == expected
