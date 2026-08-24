"""Every BOSS 直聘 selector lives here - and nowhere else.

When the site changes its markup, this is the only file that should need
editing. Nothing outside this module may hard-code a BOSS CSS selector.

Each field is a *list of candidates tried in order*, so a single markup tweak
degrades one field instead of breaking the whole capture. Candidates are kept
short and semantic; long generated CSS paths
(``div > div:nth-child(3) > span``) are deliberately avoided because they break
on any layout change.
"""

from __future__ import annotations

import re

#: Job-detail URL shapes. BOSS uses /job_detail/<id>.html and a few variants.
JOB_URL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"/job_detail/(?P<id>[A-Za-z0-9_~\-]+)\.html"),
    re.compile(r"/jobs/detail/(?P<id>[A-Za-z0-9_~\-]+)"),
)

#: Pages that are definitely not a single job posting.
NON_JOB_PATH_HINTS: tuple[str, ...] = (
    "/web/geek/job",       # search results list
    "/web/geek/chat",      # recruiter conversations - never captured
    "/web/geek/recommend",
    "/web/user",
    "/web/common/security-check",
    "/login",
    "/wapi/",
    "/c",                  # company pages
)

# --------------------------------------------------------------------------
# field selectors
# --------------------------------------------------------------------------

TITLE: tuple[str, ...] = (
    ".job-banner .name h1",
    ".job-primary .name h1",
    ".job-detail-box .job-name",
    ".job-banner h1",
    "h1.name",
    ".job-title .name",
)

SALARY: tuple[str, ...] = (
    ".job-banner .salary",
    ".job-primary .salary",
    ".job-detail-box .job-salary",
    ".salary-text",
    "span.salary",
)

COMPANY: tuple[str, ...] = (
    ".job-banner .company-info .name",
    ".sider-company .company-info .name",
    ".job-sider .company-info a.name",
    ".company-info .company-name",
    ".job-detail-company .company-name",
    "a.company-name",
)

#: The tag strip that holds city / experience / education, e.g.
#: "北京 朝阳区 · 3-5年 · 本科".
INFO_TAGS: tuple[str, ...] = (
    ".job-banner .job-primary .text-desc",
    ".job-banner .info-primary p",
    ".job-detail-box .job-tags span",
    ".job-primary .info-primary p",
    ".job-banner p",
)

#: The job description body. First match wins, so keep the tightest first.
DESCRIPTION: tuple[str, ...] = (
    ".job-detail-section .job-sec-text",
    ".job-sec .job-sec-text",
    ".job-sec-text",
    ".job-detail .text",
    ".detail-content .job-sec .text",
    ".job-detail-box .job-detail-desc",
)

#: Optional extras. Missing values must never fail a capture.
COMPANY_INDUSTRY: tuple[str, ...] = (
    ".sider-company .company-info .industry",
    ".job-sider .company-industry",
    ".company-info .industry",
)

COMPANY_SIZE: tuple[str, ...] = (
    ".sider-company .company-info .scale",
    ".job-sider .company-scale",
    ".company-info .scale",
)

RECRUITER_NAME: tuple[str, ...] = (
    ".job-boss-info .name",
    ".boss-info .name",
    ".job-author .name",
)

#: Containers stripped out of the description before storage.
NOISE_WITHIN_DESCRIPTION: tuple[str, ...] = (
    "script",
    "style",
    ".job-similar",
    ".similar-job",
    ".recommend-job",
    ".job-recommend",
)

# --------------------------------------------------------------------------
# text parsing
# --------------------------------------------------------------------------

#: "3-5年" / "1年以内" / "经验不限" / "应届生"
EXPERIENCE_RE = re.compile(
    r"(经验不限|应届生?|在校[生/]?|\d{1,2}\s*-\s*\d{1,2}\s*年|\d{1,2}\s*年以[上内]|\d{1,2}\s*年)"
)

#: Education levels BOSS uses, longest first so "大专" wins over "专".
EDUCATION_TOKENS: tuple[str, ...] = (
    "学历不限",
    "初中及以下",
    "中专/中技",
    "高中",
    "大专",
    "本科",
    "硕士",
    "博士",
)

#: Separators used inside the tag strip.
TAG_SPLIT_RE = re.compile(r"[·|｜]|\s{2,}")

#: Verification / anti-bot interstitials. We detect these only to tell the
#: human to handle it themselves - we never attempt to solve one.
VERIFICATION_HINTS: tuple[str, ...] = (
    "security-check",
    "请完成安全验证",
    "安全验证",
    "验证码",
    "滑块验证",
    "点击验证",
    "访问过于频繁",
)
