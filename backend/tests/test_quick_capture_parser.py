"""Deterministic Quick Capture parsing.

Every fixture is synthetic text written for this repository. Nothing here
contacts a recruitment site and nothing here calls OpenAI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.schemas.quick_capture import Confidence, ExtractionMethod
from app.services.job_import_parser import (
    find_education,
    find_experience,
    find_salary,
    needs_ai_extraction,
    parse_job_text,
    score_confidence,
)
from app.services.urls import canonical_url, detect_source

FIXTURES = Path(__file__).parent / "fixtures" / "quick_capture"


def load(name: str) -> str:
    return (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# token-level extraction
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("10-20K", "10-20K"),
        ("20-30K·14薪", "20-30K·14薪"),
        ("15-25K", "15-25K"),
        ("30-50K/月", "30-50K/月"),
        ("20-35K·16薪", "20-35K·16薪"),
        ("薪资 12-18k 每月", "12-18k"),
        ("1.5-2.5万", "1.5-2.5万"),
        ("薪资面议", "薪资面议"),
        ("没有薪资信息", None),
    ],
)
def test_find_salary(text, expected):
    assert find_salary(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("经验不限", "经验不限"),
        ("1年以内", "1年以内"),
        ("1-3年", "1-3年"),
        ("3-5年", "3-5年"),
        ("5-10年", "5-10年"),
        ("10年以上", "10年以上"),
        ("经验3-5年", "3-5年"),
        ("应届生", "应届生"),
        ("无相关信息", None),
    ],
)
def test_find_experience(text, expected):
    assert find_experience(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("学历不限", "学历不限"),
        ("大专", "大专"),
        ("本科", "本科"),
        ("硕士", "硕士"),
        ("博士", "博士"),
        ("学历本科", "本科"),
        ("中专/中技", "中专/中技"),
        ("没写学历", None),
    ],
)
def test_find_education(text, expected):
    assert find_education(text) == expected


# --------------------------------------------------------------------------
# whole-page parsing, one test per site style
# --------------------------------------------------------------------------


def test_parse_boss_like_text():
    candidate, trace = parse_job_text(load("boss_like"))

    assert candidate.title == "DevOps工程师"
    assert candidate.company == "杭州星澜科技有限公司"
    assert candidate.city == "杭州"
    assert candidate.salary_text == "20-30K·15薪"
    assert candidate.experience_text == "3-5年"
    assert candidate.education_text == "本科"
    assert trace.nav_lines_removed >= 5
    assert candidate.confidence.overall is Confidence.high
    assert candidate.extraction_method is ExtractionMethod.deterministic


def test_parse_liepin_like_text():
    """Different layout: metadata joined as 上海-浦东新区 | 5-10年 | 硕士."""
    candidate, _ = parse_job_text(load("liepin_like"))

    assert candidate.title == "云平台架构师"
    assert candidate.city == "上海"
    assert candidate.salary_text == "25-40K·14薪"
    assert candidate.experience_text == "5-10年"
    assert candidate.education_text == "硕士"


def test_parse_zhaopin_like_text():
    candidate, _ = parse_job_text(load("zhaopin_like"))

    assert candidate.title == "运维开发工程师"
    assert candidate.city == "北京"
    assert candidate.salary_text == "15-25K"
    assert candidate.experience_text == "3-5年"
    assert candidate.education_text == "本科"


def test_parse_job51_like_text():
    candidate, _ = parse_job_text(load("job51_like"))

    assert candidate.title == "云计算运维工程师"
    assert candidate.city == "广州"
    assert candidate.salary_text == "12-18K/月"
    assert candidate.experience_text == "1-3年"
    assert candidate.education_text == "大专"


def test_parser_is_not_tied_to_any_one_site():
    """The same code path handles all four styles with no site hint."""
    for name in ("boss_like", "liepin_like", "zhaopin_like", "job51_like"):
        candidate, _ = parse_job_text(load(name))
        assert candidate.title, f"{name} lost its title"
        assert candidate.raw_description, f"{name} lost its description"
        assert candidate.source == "manual", "no URL supplied -> manual"


# --------------------------------------------------------------------------
# description isolation / noise removal
# --------------------------------------------------------------------------


def test_description_keeps_the_job_body():
    candidate, trace = parse_job_text(load("boss_like"))
    body = candidate.raw_description

    assert trace.description_heading == "职位描述"
    assert "AWS" in body and "Terraform" in body and "Kubernetes" in body
    assert "任职要求" in body, "requirements are part of the JD, not a trailer"


@pytest.mark.parametrize(
    ("fixture", "noise"),
    [
        ("boss_like", ["首页", "立即沟通", "相似职位", "职位福利", "张女士", "带薪年假"]),
        ("liepin_like", ["猎聘", "公司介绍", "举报该职位"]),
        ("zhaopin_like", ["智联招聘", "福利待遇", "相关职位", "六险一金"]),
        ("job51_like", ["前程无忧51job", "联系方式", "上班地址"]),
    ],
)
def test_page_noise_never_reaches_the_description(fixture, noise):
    candidate, _ = parse_job_text(load(fixture))
    for token in noise:
        assert token not in candidate.raw_description, f"{token!r} leaked into the JD"


def test_navigation_removal_is_reported_as_a_warning():
    candidate, trace = parse_job_text(load("boss_like"))
    assert trace.nav_lines_removed > 0
    assert any("导航" in w for w in candidate.warnings)


def test_jd_lines_containing_nav_words_are_kept():
    """Only standalone nav tokens are dropped, never JD text that mentions them."""
    text = (
        "云运维工程师\n20-30K\n杭州\n3-5年\n本科\n\n职位描述\n"
        "1. 负责公司职位管理系统的运维，保障消息推送链路稳定；\n"
        "2. 维护首页相关服务的可用性，处理线上故障；\n"
        "3. 编写自动化脚本，减少重复工作，提升交付效率。\n"
    )
    candidate, _ = parse_job_text(text)
    assert "职位管理系统" in candidate.raw_description
    assert "首页相关服务" in candidate.raw_description


def test_parser_survives_text_with_no_headings():
    text = (
        "某某科技有限公司\n高级运维工程师\n25-35K\n深圳\n3-5年\n本科\n"
        "负责云平台运维工作，维护 Kubernetes 集群与 CI/CD 流水线，"
        "编写 Python 自动化脚本，参与故障排查与性能优化，保障线上服务稳定运行。\n"
    )
    candidate, trace = parse_job_text(text)
    assert trace.description_heading is None
    assert "Kubernetes" in candidate.raw_description
    assert any("最长文本块" in w for w in candidate.warnings)


# --------------------------------------------------------------------------
# missing fields / confidence
# --------------------------------------------------------------------------


def test_missing_fields_do_not_raise():
    candidate, _ = parse_job_text(load("noisy_minimal"))
    assert candidate.title is None
    assert candidate.salary_text is None
    assert candidate.confidence.overall is Confidence.low
    assert any("职位标题" in w for w in candidate.warnings)
    assert candidate.is_saveable() is False


def test_empty_input_returns_an_empty_candidate():
    candidate, _ = parse_job_text("")
    assert candidate.raw_description == ""
    assert candidate.is_saveable() is False


def test_confidence_reflects_how_a_value_was_found():
    strong = score_confidence(
        company="某某科技有限公司",
        title="DevOps工程师",
        city="杭州",
        salary="20-30K",
        experience="3-5年",
        description="职位描述" * 40,
        found_heading=True,
    )
    assert strong.title is Confidence.high
    assert strong.company is Confidence.high
    assert strong.city is Confidence.high
    assert strong.salary is Confidence.high
    assert strong.overall is Confidence.high

    weak = score_confidence(
        company=None,
        title=None,
        city=None,
        salary=None,
        experience=None,
        description="",
        found_heading=False,
    )
    assert weak.overall is Confidence.low
    assert weak.description is Confidence.low


def test_unknown_city_is_medium_not_high():
    conf = score_confidence(
        company=None,
        title="工程师",
        city="某某新区",
        salary=None,
        experience=None,
        description="x" * 200,
        found_heading=True,
    )
    assert conf.city is Confidence.medium


# --------------------------------------------------------------------------
# AI fallback decision
# --------------------------------------------------------------------------


def test_good_deterministic_result_does_not_need_ai():
    candidate, _ = parse_job_text(load("boss_like"))
    assert needs_ai_extraction(candidate) is False


def test_weak_deterministic_result_needs_ai():
    candidate, _ = parse_job_text(load("noisy_minimal"))
    assert needs_ai_extraction(candidate) is True


def test_ai_needed_when_metadata_is_thin():
    """Title + description are fine, but fewer than two metadata fields."""
    text = "系统运维工程师\n\n职位描述\n" + ("负责服务器日常运维与故障处理。" * 12)
    candidate, _ = parse_job_text(text)
    assert candidate.confidence.title is Confidence.high
    assert needs_ai_extraction(candidate) is True


# --------------------------------------------------------------------------
# source URL handling (metadata only - never fetched)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.zhipin.com/job_detail/abc.html", "boss"),
        ("https://www.liepin.com/job/1234.shtml", "liepin"),
        ("https://jobs.51job.com/hangzhou/123.html", "job51"),
        ("https://i.zhaopin.com/job/456", "zhaopin"),
        ("https://example.com/careers/1", "manual"),
        ("not a url", "manual"),
        ("", "manual"),
        (None, "manual"),
    ],
)
def test_detect_source_is_deterministic(url, expected):
    assert detect_source(url) == expected
    assert detect_source(url) == expected  # same answer every time


def test_source_url_query_is_stripped_before_persistence():
    dirty = "https://www.zhipin.com/job_detail/abc.html?lid=xyz&securityId=secret&utm_source=x"
    candidate, _ = parse_job_text(load("boss_like"), source_url=dirty)

    assert candidate.source == "boss"
    assert candidate.source_url == "https://www.zhipin.com/job_detail/abc.html"
    assert "securityId" not in candidate.source_url
    assert "utm_source" not in candidate.source_url


def test_canonical_url_drops_fragments_too():
    assert canonical_url("https://a.com/b?c=1#frag") == "https://a.com/b"
