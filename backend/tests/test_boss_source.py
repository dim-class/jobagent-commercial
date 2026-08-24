"""BossJobSource: URL recognition and extraction from local HTML fixtures.

These tests drive a REAL Playwright browser, but only ever against the static
HTML in ``tests/fixtures``. Nothing here contacts zhipin.com, and nothing here
logs in, applies, or messages anyone.

The fixture browser runs headless purely because it is a test harness. The
recruitment browser the user drives (``app.services.browser_session``) is
always headed - that is a product rule, not an implementation detail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.job_sources import boss_source
from app.job_sources.base import BrowserJobSource, JobSearchQuery, JobSource
from app.job_sources.boss.extractor import (
    NotAJobPageError,
    VerificationRequiredError,
    extract_job,
    parse_external_id,
    parse_info_tags,
)

FIXTURES = Path(__file__).parent / "fixtures"

JOB_URL = "https://www.zhipin.com/job_detail/a1b2c3d4e5f6~.html?lid=abc&securityId=xyz"


def fixture_html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# URL recognition (no browser needed)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (JOB_URL, True),
        ("https://www.zhipin.com/job_detail/abc123.html", True),
        ("https://zhipin.com/job_detail/abc123.html", True),
        # supported host, but not a job detail page
        ("https://www.zhipin.com/web/geek/job?query=cloud", False),
        ("https://www.zhipin.com/web/geek/chat", False),
        ("https://www.zhipin.com/", False),
        # other sites
        ("https://www.liepin.com/job/123.html", False),
        ("https://www.zhaopin.com/job_detail/123.html", False),
        # never capture browser-internal pages
        ("chrome://settings", False),
        ("about:blank", False),
        ("chrome-extension://abcdef/page.html", False),
        ("", False),
    ],
)
def test_supports_url(url, expected):
    assert boss_source.supports_url(url) is expected


def test_supports_host_is_broader_than_supports_url():
    """The search page is on a supported host but is not capturable."""
    search = "https://www.zhipin.com/web/geek/job?query=cloud"
    assert boss_source.supports_host(search) is True
    assert boss_source.supports_url(search) is False


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (JOB_URL, "a1b2c3d4e5f6~"),
        ("https://www.zhipin.com/job_detail/abc123.html", "abc123"),
        ("https://www.zhipin.com/web/geek/job", None),
        ("", None),
    ],
)
def test_parse_external_id(url, expected):
    assert parse_external_id(url) == expected


def test_external_id_is_stable_across_query_strings():
    """The session tokens BOSS appends must not change the job identity."""
    a = parse_external_id("https://www.zhipin.com/job_detail/xyz.html?lid=1&securityId=aaa")
    b = parse_external_id("https://www.zhipin.com/job_detail/xyz.html?lid=2&securityId=bbb")
    assert a == b == "xyz"


# --------------------------------------------------------------------------
# tag-strip parsing (no browser needed)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        (["北京 朝阳区 · 3-5年 · 本科"], ("北京", "3-5年", "本科")),
        (["上海", "1-3年", "大专"], ("上海", "1-3年", "大专")),
        (["杭州 · 经验不限 · 学历不限"], ("杭州", "经验不限", "学历不限")),
        (["广州 · 5年以上 · 硕士"], ("广州", "5年以上", "硕士")),
        ([], (None, None, None)),
    ],
)
def test_parse_info_tags(tags, expected):
    assert parse_info_tags(tags) == expected


# --------------------------------------------------------------------------
# JobSource contract
# --------------------------------------------------------------------------


def test_boss_source_implements_both_interfaces():
    assert isinstance(boss_source, JobSource)
    assert isinstance(boss_source, BrowserJobSource)
    assert boss_source.name == "boss"
    assert boss_source.implemented is True


def test_boss_source_does_not_crawl():
    """Mass collection is out of scope: the human picks every posting."""
    assert boss_source.search_jobs(JobSearchQuery(keywords=["cloud"])) == []
    assert boss_source.get_job("anything") is None


# --------------------------------------------------------------------------
# extraction against local fixtures (real Playwright, local HTML only)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_standard_job(fixture_page):
    page = await fixture_page("boss_job_standard.html", url=JOB_URL)
    posting, report = await extract_job(page, url=JOB_URL)

    assert posting.title == "云计算工程师"
    assert posting.company == "示例科技（虚构公司）"
    assert posting.city == "北京"
    assert posting.salary_text == "20-30K·13薪"
    assert posting.experience_text == "3-5年"
    assert posting.education_text == "本科"
    assert posting.external_id == "a1b2c3d4e5f6~"

    assert "岗位职责：" in posting.raw_description
    assert "Terraform" in posting.raw_description
    assert "任职要求：" in posting.raw_description
    assert report.description_chars > 100
    assert report.fields_missing == []


@pytest.mark.asyncio
async def test_extraction_excludes_page_chrome(fixture_page):
    """Nav, footer and recommended jobs must not land in the stored JD."""
    page = await fixture_page("boss_job_standard.html", url=JOB_URL)
    posting, _ = await extract_job(page, url=JOB_URL)

    jd = posting.raw_description
    assert "相似职位推荐" not in jd
    assert "不该被采集" not in jd
    assert "隐私政策" not in jd
    assert "用户协议" not in jd


@pytest.mark.asyncio
async def test_extract_optional_metadata(fixture_page):
    page = await fixture_page("boss_job_standard.html", url=JOB_URL)
    posting, _ = await extract_job(page, url=JOB_URL)
    assert posting.extra["company_industry"] == "计算机软件"
    assert posting.extra["company_size"] == "500-999人"
    assert posting.extra["recruiter_name"] == "李招聘·HR"


@pytest.mark.asyncio
async def test_extract_tolerates_missing_optional_fields(fixture_page):
    """Only title + description are required; the rest may be absent."""
    page = await fixture_page("boss_job_missing_optional.html", url=JOB_URL)
    posting, report = await extract_job(page, url=JOB_URL)

    assert posting.title == "运维开发工程师"
    assert posting.raw_description
    assert posting.company == ""
    assert posting.city is None
    assert posting.salary_text is None
    assert set(report.fields_missing) >= {"company", "city", "salary_text"}
    assert report.fields_found == ["title"]


@pytest.mark.asyncio
async def test_non_job_page_is_rejected(fixture_page):
    page = await fixture_page("boss_not_job.html", url="https://www.zhipin.com/web/geek/job")
    with pytest.raises(NotAJobPageError):
        await extract_job(page, url="https://www.zhipin.com/web/geek/job")


@pytest.mark.asyncio
async def test_verification_page_is_detected_not_solved(fixture_page):
    """We surface the challenge to the human; we never try to clear it."""
    page = await fixture_page("boss_verification.html", url=JOB_URL)
    with pytest.raises(VerificationRequiredError):
        await extract_job(page, url=JOB_URL)


@pytest.mark.asyncio
async def test_capture_from_page_returns_a_posting(fixture_page):
    page = await fixture_page("boss_job_standard.html", url=JOB_URL)
    posting = await boss_source.capture_from_page(page)
    assert posting.title == "云计算工程师"


@pytest.mark.asyncio
async def test_captured_posting_normalizes_like_any_other_source(fixture_page):
    """The v0.1 pipeline must treat a captured job identically to a pasted one."""
    page = await fixture_page("boss_job_standard.html", url=JOB_URL)
    posting, _ = await extract_job(page, url=JOB_URL)

    normalized = boss_source.normalize_job(posting)
    assert normalized.city == "北京"
    assert len(normalized.content_hash) == 64
    assert "\n\n\n" not in normalized.normalized_description
    assert normalized.raw_description == posting.raw_description


@pytest.mark.asyncio
async def test_whitespace_is_normalized_on_capture(fixture_page):
    """Ragged indentation in the page must not survive into the stored JD."""
    messy = """
    <div class="job-detail-section"><div class="job-sec-text">岗位职责：


        1. 负责    云平台运维；



        2. 维护 Kubernetes 集群。
    </div></div>
    <div class="job-banner"><div class="name"><h1>  云平台工程师  </h1></div></div>
    """
    page = await fixture_page(html=messy, url=JOB_URL)
    posting, _ = await extract_job(page, url=JOB_URL)

    assert posting.title == "云平台工程师"
    assert "\n\n\n" not in posting.raw_description
    assert "负责 云平台运维；" in posting.raw_description

    normalized = boss_source.normalize_job(posting)
    assert normalized.normalized_description == normalized.normalized_description.strip()


@pytest.mark.asyncio
async def test_blank_page_is_reported_as_empty_not_as_a_missing_job(fixture_page):
    """A job URL that renders nothing means the site withheld content.

    We surface that honestly instead of guessing; bypassing such a restriction
    is out of scope by policy.
    """
    from app.job_sources.boss.extractor import PageEmptyError

    page = await fixture_page("boss_job_blank.html", url=JOB_URL)
    with pytest.raises(PageEmptyError):
        await extract_job(page, url=JOB_URL)
