"""Deterministic parsing of job text a human copied out of their own browser.

Input is whatever ``Ctrl+A / Ctrl+C`` produced: nav bars, the job header, the
JD, then福利 / 相似职位 / HR blocks. We pull out the fields we can prove and say
honestly how confident we are; the user reviews everything before it is saved.

This module is site-agnostic on purpose. It keys off Chinese recruitment
*conventions* (salary/experience/education token shapes, section headings), not
off any one site's markup - unlike ``job_sources/boss/selectors.py``, which is
tied to BOSS's DOM. That is why the same parser handles BOSS, 猎聘, 智联 and
51job text without knowing which one it is looking at.

Nothing here fetches anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.schemas.quick_capture import (
    Confidence,
    ExtractionMethod,
    FieldConfidence,
    JobImportCandidate,
)
from app.services.urls import canonical_url, detect_source

# --------------------------------------------------------------------------
# salary
# --------------------------------------------------------------------------

# 20-30K·15薪 / 10-20K / 15-25k / 30-50K/月 / 20-35K·16薪
_SALARY_K = r"\d{1,3}\s*[-~～]\s*\d{1,3}\s*[Kk千]"
# 1.2-2万 / 8千-1.2万 / 10万-15万
_SALARY_WAN = r"\d{1,3}(?:\.\d)?\s*[千万]?\s*[-~～]\s*\d{1,3}(?:\.\d)?\s*[千万]"
_SALARY_SUFFIX = r"(?:\s*[·*×]\s*\d{1,2}\s*薪|\s*/\s*[月年天日]|\s*元\s*/\s*[月年天日])?"

SALARY_RE = re.compile(rf"(?:{_SALARY_K}|{_SALARY_WAN}){_SALARY_SUFFIX}")
# Longest first, so "薪资面议" is not truncated to "面议".
SALARY_NEGOTIABLE = ("薪资面议", "待遇面议", "面议")

# --------------------------------------------------------------------------
# experience / education
# --------------------------------------------------------------------------

EXPERIENCE_RE = re.compile(
    r"(经验不限|不限经验|应届生?|在校生?|实习生?"
    r"|\d{1,2}\s*年以内|\d{1,2}\s*年以下"
    r"|\d{1,2}\s*[-~～]\s*\d{1,2}\s*年"
    r"|\d{1,2}\s*年以上"
    r"|\d{1,2}\s*年)"
)

# Longest first so 大专 wins over 专, 中专/中技 over 中专.
EDUCATION_TOKENS: tuple[str, ...] = (
    "学历不限",
    "初中及以下",
    "中专/中技",
    "中专",
    "高中",
    "大专",
    "本科",
    "硕士",
    "博士",
    "MBA",
)

# --------------------------------------------------------------------------
# city
# --------------------------------------------------------------------------

# A seed list, not a closed universe: any "<2-4 chars>市" or a token followed by
# a known district suffix is also accepted (see :func:`_looks_like_city`).
KNOWN_CITIES: tuple[str, ...] = (
    "北京", "上海", "广州", "深圳", "杭州", "南京", "苏州", "成都", "武汉", "西安",
    "天津", "重庆", "长沙", "郑州", "青岛", "大连", "宁波", "厦门", "合肥", "福州",
    "济南", "无锡", "佛山", "东莞", "昆明", "沈阳", "哈尔滨", "长春", "石家庄",
    "太原", "南昌", "贵阳", "南宁", "常州", "temp",
)

# Common districts. Also a seed list - anything ending in 区/县/新区 counts.
KNOWN_DISTRICTS: tuple[str, ...] = (
    "朝阳", "海淀", "东城", "西城", "丰台", "昌平", "大兴",
    "浦东", "徐汇", "静安", "黄浦", "长宁", "杨浦", "闵行",
    "天河", "越秀", "海珠", "番禺", "白云", "黄埔",
    "余杭", "滨江", "西湖", "拱墅", "萧山", "上城", "下城",
    "南山", "福田", "罗湖", "宝安", "龙岗", "龙华",
    "雨花", "岳麓", "武侯", "高新", "锦江", "江干",
)

_DISTRICT_SUFFIX_RE = re.compile(r".{1,4}(区|县|新区|开发区)$")
_CITY_SUFFIX_RE = re.compile(r"^(.{1,4})市$")

# --------------------------------------------------------------------------
# section headings
# --------------------------------------------------------------------------

DESCRIPTION_HEADINGS: tuple[str, ...] = (
    "职位描述", "职位详情", "岗位描述", "岗位详情", "职位介绍",
    "岗位职责", "工作职责", "职责描述", "工作内容", "岗位内容",
    "职位要求", "岗位要求", "任职要求", "任职资格", "招聘要求",
    "job description", "responsibilities", "requirements",
)

#: Everything at or after one of these belongs to the page, not the job.
STOP_HEADINGS: tuple[str, ...] = (
    "职位福利", "公司福利", "福利待遇", "工作地址", "上班地址", "地址",
    "相似职位", "相关职位", "推荐职位", "为你推荐", "猜你喜欢", "该公司其他职位",
    "公司介绍", "公司信息", "公司简介", "企业介绍", "工商信息",
    "举报该职位", "举报", "投诉", "分享", "收藏职位",
    "联系方式", "联系人", "求职反馈", "面试评价", "安全提示",
)

#: Standalone navigation/chrome lines. Matched exactly, so a JD line that
#: merely contains "职位" is never dropped.
NAV_TOKENS: frozenset[str] = frozenset(
    {
        "首页", "职位", "推荐", "沟通", "消息", "我的", "简历", "搜索", "登录", "注册",
        "退出", "设置", "通知", "收藏", "反馈", "帮助", "客服", "下载", "app", "APP",
        "找工作", "招聘", "公司", "校园招聘", "兼职", "更多", "全部", "筛选", "排序",
        "立即沟通", "立即申请", "投递简历", "在线沟通", "打招呼", "继续沟通",
        "上一页", "下一页", "返回", "关闭", "展开", "收起", "查看更多",
        "BOSS直聘", "猎聘", "智联招聘", "前程无忧", "51job",
    }
)

_HR_LINE_RE = re.compile(r"^(HR|hr|人事|招聘者|BOSS)\b|(先生|女士|经理|HRBP|HRD)$")
_COMPANY_HINT_RE = re.compile(
    r"(有限公司|股份公司|集团|科技|网络|信息技术|软件|数据|传媒|银行|研究院|事业部|"
    r"Technology|Inc\.?|Ltd\.?|Corp\.?|Co\.,)"
)
_TITLE_HINT_RE = re.compile(
    r"(工程师|开发|研发|运维|架构师|经理|主管|专员|总监|顾问|分析师|设计师|"
    r"实习生|助理|测试|算法|产品|运营|销售|讲师|DBA|SRE|DevOps|Engineer|Developer|"
    r"Manager|Architect|Analyst|Designer|Specialist|Lead)",
    re.IGNORECASE,
)

MIN_DESCRIPTION_CHARS = 40
MAX_TEXT_CHARS = 60_000


@dataclass(slots=True)
class _Line:
    text: str
    index: int
    is_nav: bool = False


@dataclass(slots=True)
class ParseTrace:
    """Why the parser decided what it decided - handy in tests and debugging."""

    nav_lines_removed: int = 0
    description_heading: str | None = None
    stop_heading: str | None = None
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _clean_lines(text: str) -> list[str]:
    out: list[str] = []
    for raw in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = re.sub(r"[ \t 　]+", " ", raw).strip()
        out.append(line)
    return out


def _is_nav(line: str) -> bool:
    if not line:
        return False
    if line in NAV_TOKENS:
        return True
    # "首页 · 职位 · 消息" style breadcrumbs
    parts = [p for p in re.split(r"[|·/>»\s]+", line) if p]
    return len(parts) > 1 and all(p in NAV_TOKENS for p in parts)


def _looks_like_city(token: str) -> bool:
    token = token.strip()
    if not token or len(token) > 8:
        return False
    if token in KNOWN_CITIES or token in KNOWN_DISTRICTS:
        return True
    if _CITY_SUFFIX_RE.match(token):
        return True
    return bool(_DISTRICT_SUFFIX_RE.match(token))


def _split_tokens(line: str) -> list[str]:
    """Split a metadata strip into candidate tokens.

    Hyphens are separators too: sites write the location as ``上海-浦东新区``.
    Splitting ``20-30K`` as a side effect is harmless - the callers only ask
    whether a token *looks like* a city.
    """
    return [t for t in re.split(r"[\s|·•｜/、,，\-－—]+", line) if t]


def find_salary(text: str) -> str | None:
    match = SALARY_RE.search(text or "")
    if match:
        return re.sub(r"\s+", "", match.group(0))
    for token in SALARY_NEGOTIABLE:
        if token in (text or ""):
            return token
    return None


def find_experience(text: str) -> str | None:
    match = EXPERIENCE_RE.search(text or "")
    return re.sub(r"\s+", "", match.group(0)) if match else None


def find_education(text: str) -> str | None:
    for token in EDUCATION_TOKENS:
        if token in (text or ""):
            return token
    return None


def find_city(lines: list[str]) -> str | None:
    """First token that reads like a city, preferring a real city over a district."""
    district_fallback: str | None = None
    for line in lines:
        for token in _split_tokens(line):
            token = token.strip("：:（）()[]")
            if token in KNOWN_CITIES:
                return token
            match = _CITY_SUFFIX_RE.match(token)
            if match:
                return match.group(1)
            if district_fallback is None and (
                token in KNOWN_DISTRICTS or _DISTRICT_SUFFIX_RE.match(token)
            ):
                district_fallback = token
    return district_fallback


# --------------------------------------------------------------------------
# description isolation
# --------------------------------------------------------------------------


def _heading_at(line: str, headings: tuple[str, ...]) -> str | None:
    """A heading line is short and is (essentially) just the heading."""
    stripped = line.strip().strip("：:【】[]#*-— ")
    if not stripped or len(stripped) > 16:
        return None
    lowered = stripped.lower()
    for heading in headings:
        if lowered == heading.lower():
            return heading
    return None


def extract_description(lines: list[str], trace: ParseTrace) -> str:
    """Pull the JD body out of a full page dump.

    Preferred: everything between a description heading and the first stop
    heading. Fallback: the longest run of substantial lines, which keeps the
    parser useful on sites whose headings we do not recognise.
    """
    start = None
    for i, line in enumerate(lines):
        heading = _heading_at(line, DESCRIPTION_HEADINGS)
        if heading:
            start = i
            trace.description_heading = heading
            break

    if start is not None:
        body: list[str] = []
        # Keep later headings (职位要求 after 岗位职责) as part of the body.
        for line in lines[start + 1 :]:
            stop = _heading_at(line, STOP_HEADINGS)
            if stop:
                trace.stop_heading = stop
                break
            if _HR_LINE_RE.search(line):
                trace.stop_heading = "HR"
                break
            body.append(line)
        text = _collapse("\n".join(body))
        if len(text) >= MIN_DESCRIPTION_CHARS:
            return text

    return _longest_block(lines)


def _longest_block(lines: list[str]) -> str:
    """Longest contiguous run of non-nav, non-trivial lines."""
    best: list[str] = []
    current: list[str] = []
    for line in lines:
        if not line or _is_nav(line) or _heading_at(line, STOP_HEADINGS):
            if len("".join(current)) > len("".join(best)):
                best = current
            current = []
            continue
        current.append(line)
    if len("".join(current)) > len("".join(best)):
        best = current
    return _collapse("\n".join(best))


def _collapse(text: str) -> str:
    lines = [line.rstrip() for line in text.split("\n")]
    out: list[str] = []
    blank = 0
    for line in lines:
        if line.strip():
            blank = 0
            out.append(line)
        else:
            blank += 1
            if blank <= 1:
                out.append("")
    return "\n".join(out).strip()


# --------------------------------------------------------------------------
# header fields
# --------------------------------------------------------------------------


def _find_company(lines: list[str]) -> str | None:
    for line in lines:
        if 2 <= len(line) <= 40 and _COMPANY_HINT_RE.search(line):
            return line.strip("：: ")
    return None


def _find_title(lines: list[str], salary_index: int | None, company: str | None) -> str | None:
    """The title sits next to the salary in every layout we have seen."""

    def usable(line: str) -> bool:
        return (
            bool(line)
            and line != company
            and 2 <= len(line) <= 40
            and not _is_nav(line)
            and not SALARY_RE.search(line)
            and not _heading_at(line, DESCRIPTION_HEADINGS)
            and not _heading_at(line, STOP_HEADINGS)
        )

    if salary_index is not None:
        # Same line: "DevOps工程师 20-30K"
        same = SALARY_RE.sub("", lines[salary_index]).strip(" ·|｜-")
        if usable(same) and _TITLE_HINT_RE.search(same):
            return same
        for offset in (-1, -2, 1):
            i = salary_index + offset
            if 0 <= i < len(lines) and usable(lines[i]) and _TITLE_HINT_RE.search(lines[i]):
                return lines[i]

    for line in lines:
        if usable(line) and _TITLE_HINT_RE.search(line):
            return line
    return None


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def parse_job_text(
    text: str, *, source_url: str | None = None
) -> tuple[JobImportCandidate, ParseTrace]:
    """Best-effort deterministic parse. Never raises on messy input."""
    trace = ParseTrace()
    raw_lines = _clean_lines((text or "")[:MAX_TEXT_CHARS])

    kept: list[str] = []
    for line in raw_lines:
        if _is_nav(line):
            trace.nav_lines_removed += 1
            continue
        kept.append(line)

    non_empty = [line for line in kept if line]
    header = non_empty[:12]  # the metadata strip lives near the top

    salary_index = next(
        (i for i, line in enumerate(kept) if SALARY_RE.search(line)),
        None,
    )

    joined_header = "\n".join(header)
    salary = find_salary(joined_header) or find_salary("\n".join(non_empty[:30]))
    experience = find_experience(joined_header) or find_experience("\n".join(non_empty[:30]))
    education = find_education(joined_header) or find_education("\n".join(non_empty[:30]))
    city = find_city(header)
    company = _find_company(non_empty[:20])
    title = _find_title(kept, salary_index, company)
    description = extract_description(kept, trace)

    warnings: list[str] = []
    if trace.nav_lines_removed:
        warnings.append("已自动移除疑似页面导航文字，请检查职位描述是否完整")
    if not title:
        warnings.append("未能识别职位标题，请手动补充后保存")
    if not description or len(description) < MIN_DESCRIPTION_CHARS:
        warnings.append("职位描述内容较少，可能没有复制完整")
    if not salary:
        warnings.append("未识别到薪资信息")
    if trace.description_heading is None:
        warnings.append("未找到「职位描述」等标题，已按最长文本块推断描述范围")

    candidate = JobImportCandidate(
        source=detect_source(source_url),
        source_url=canonical_url(source_url),
        company=company,
        title=title,
        city=city,
        salary_text=salary,
        experience_text=experience,
        education_text=education,
        raw_description=description,
        confidence=score_confidence(
            company=company,
            title=title,
            city=city,
            salary=salary,
            experience=experience,
            description=description,
            found_heading=trace.description_heading is not None,
        ),
        warnings=warnings,
        extraction_method=ExtractionMethod.deterministic,
    )
    return candidate, trace


def score_confidence(
    *,
    company: str | None,
    title: str | None,
    city: str | None,
    salary: str | None,
    experience: str | None,
    description: str,
    found_heading: bool,
) -> FieldConfidence:
    """Confidence reflects *how* a value was found, not just whether it exists.

    Token-shaped fields (salary/experience) are high when their regex matched,
    because those patterns are unambiguous. Free-text fields (company/title) are
    at most medium, since they come from heuristics the user should eyeball.
    """
    title_conf = Confidence.low
    if title:
        title_conf = Confidence.high if _TITLE_HINT_RE.search(title) else Confidence.medium

    company_conf = Confidence.low
    if company:
        company_conf = Confidence.high if _COMPANY_HINT_RE.search(company) else Confidence.medium

    city_conf = Confidence.low
    if city:
        city_conf = Confidence.high if city in KNOWN_CITIES else Confidence.medium

    salary_conf = Confidence.high if salary and SALARY_RE.search(salary) else (
        Confidence.medium if salary else Confidence.low
    )
    exp_conf = Confidence.high if experience else Confidence.low

    if not description or len(description) < MIN_DESCRIPTION_CHARS:
        desc_conf = Confidence.low
    elif found_heading and len(description) >= 120:
        desc_conf = Confidence.high
    else:
        desc_conf = Confidence.medium

    strong = sum(
        1
        for c in (company_conf, city_conf, salary_conf, exp_conf)
        if c is Confidence.high
    )
    if title_conf is Confidence.high and desc_conf is Confidence.high and strong >= 2:
        overall = Confidence.high
    elif title_conf is not Confidence.low and desc_conf is not Confidence.low:
        overall = Confidence.medium
    else:
        overall = Confidence.low

    return FieldConfidence(
        overall=overall,
        company=company_conf,
        title=title_conf,
        city=city_conf,
        salary=salary_conf,
        experience=exp_conf,
        description=desc_conf,
    )


def needs_ai_extraction(candidate: JobImportCandidate) -> bool:
    """Whether the deterministic result is too weak to show as-is.

    Policy (see the v0.3 brief): deterministic output is sufficient when the
    title and the description are both high confidence AND at least two
    metadata fields were confidently extracted. Anything less earns one AI pass.
    """
    conf = candidate.confidence
    if conf.title is not Confidence.high or conf.description is not Confidence.high:
        return True
    metadata_high = sum(
        1
        for c in (conf.company, conf.city, conf.salary, conf.experience)
        if c is Confidence.high
    )
    return metadata_high < 2
