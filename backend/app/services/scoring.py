"""Deterministic pre-analysis features + post-analysis guardrails.

The LLM never sees a raw JD alone. It receives a computed feature block -
city match, title-family match, skill overlap, excluded-role hits, extracted
experience requirement - and is asked to *interpret* those signals. That keeps
the score anchored to facts instead of model mood, and it makes a large part of
the pipeline unit-testable without spending a token.

Nothing here hard-rejects a job on its own. The single hard rule is the
excluded-role cap in :func:`apply_guardrails`, and even that only fires when
the JD is an excluded role *and* shows essentially no relevant skill overlap.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

# --------------------------------------------------------------------------
# experience extraction
# --------------------------------------------------------------------------

# "3-5年" / "3~5 年" / "3 to 5 years"
_RANGE_RE = re.compile(r"(\d{1,2})\s*[-~～至到]\s*(\d{1,2})\s*(?:年|years?)", re.IGNORECASE)
# "5年以上" / "至少5年" / "5+ years" / "minimum 3 years"
_MIN_RE = re.compile(
    r"(?:至少|不低于|不少于|超过|minimum(?:\s+of)?|at\s+least|over)?\s*"
    r"(\d{1,2})\s*(?:年以上|\+\s*years?|年|years?)\s*(?:以上|or\s+more)?",
    re.IGNORECASE,
)
_HARD_REQUIREMENT_MARKERS = (
    "硬性要求",
    "必须满足",
    "不满足勿投",
    "勿投",
    "硬性条件",
    "必须具备",
    "hard requirement",
    "must have at least",
    "strictly required",
)
_NO_EXPERIENCE_MARKERS = ("经验不限", "不限经验", "应届", "在校", "实习")


@dataclass(slots=True)
class ExperienceRequirement:
    min_years: int | None = None
    max_years: int | None = None
    unlimited: bool = False
    is_hard_requirement: bool = False
    evidence: str = ""

    def describe(self) -> str:
        if self.unlimited:
            return "经验不限"
        if self.min_years is None:
            return "未明确"
        if self.max_years is not None and self.max_years != self.min_years:
            base = f"{self.min_years}-{self.max_years}年"
        else:
            base = f"{self.min_years}年以上"
        return base + ("（JD 标明为硬性要求）" if self.is_hard_requirement else "")


def extract_experience_requirement(*texts: str) -> ExperienceRequirement:
    """Best-effort extraction of the required years of experience.

    Reads the explicit ``experience_text`` field first (recruiting sites give
    us one), then falls back to scanning the JD body.
    """
    blob = "\n".join(t for t in texts if t)
    if not blob:
        return ExperienceRequirement()

    lowered = blob.lower()
    hard = any(m in lowered for m in _HARD_REQUIREMENT_MARKERS)

    if any(m in blob for m in _NO_EXPERIENCE_MARKERS) and not _RANGE_RE.search(blob):
        return ExperienceRequirement(unlimited=True, is_hard_requirement=hard, evidence="经验不限")

    m = _RANGE_RE.search(blob)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo > hi:
            lo, hi = hi, lo
        return ExperienceRequirement(
            min_years=lo, max_years=hi, is_hard_requirement=hard, evidence=m.group(0).strip()
        )

    best: tuple[int, str] | None = None
    for candidate in _MIN_RE.finditer(blob):
        years = int(candidate.group(1))
        if 0 < years <= 30 and (best is None or years > best[0]):
            best = (years, candidate.group(0).strip())
    if best:
        return ExperienceRequirement(min_years=best[0], is_hard_requirement=hard, evidence=best[1])
    return ExperienceRequirement(is_hard_requirement=hard)


# --------------------------------------------------------------------------
# salary extraction
# --------------------------------------------------------------------------

# The unit is usually written once, at the end: "40-60K". Requiring a K after
# both numbers silently failed on the most common Chinese format, so the first
# one is optional ("20k-30k" still matches).
_SALARY_K_RE = re.compile(r"(\d{1,3})\s*[kK]?\s*[-~～]\s*(\d{1,3})\s*[kK]")
_SALARY_PLAIN_RE = re.compile(r"(\d{4,6})\s*[-~～]\s*(\d{4,6})")


def extract_salary_range(text: str | None) -> tuple[int | None, int | None]:
    """Return (min, max) monthly CNY when we can parse it, else (None, None)."""
    if not text:
        return (None, None)
    m = _SALARY_K_RE.search(text)
    if m:
        return (int(m.group(1)) * 1000, int(m.group(2)) * 1000)
    m = _SALARY_PLAIN_RE.search(text)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    return (None, None)


# --------------------------------------------------------------------------
# skill / role / city matching
# --------------------------------------------------------------------------


def _expand_skill_terms(skill: str, aliases: dict[str, Any]) -> list[str]:
    terms = [skill]
    extra = aliases.get(skill) or []
    if isinstance(extra, str):
        extra = [extra]
    terms.extend(str(a) for a in extra)
    return [t for t in terms if t]


# Short acronyms whose lowercase form is an ordinary English word. For these we
# demand the exact casing from the config ("WAS" the middleware, not "was" the
# verb); everything else matches case-insensitively.
_CASE_SENSITIVE_ACRONYMS = frozenset({"was", "is", "it", "as", "at", "go", "on", "in", "so"})


def contains_term(haystack: str, term: str) -> bool:
    """Substring match, with a word boundary for short ASCII acronyms.

    Without the boundary "ECS" matches "specs" and every English JD becomes a
    perfect skill match; without the case rule "WAS" matches the verb "was".
    """
    term = (term or "").strip()
    if not term:
        return False
    low = term.lower()
    if len(low) <= 4 and low.isascii() and low.isalnum():
        pattern = rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])"
        flags = 0 if low in _CASE_SENSITIVE_ACRONYMS else re.IGNORECASE
        return re.search(pattern, haystack, flags) is not None
    return low in haystack.lower()


def match_skills(
    text: str, skills: list[str], aliases: dict[str, Any]
) -> tuple[list[str], list[str]]:
    """Split ``skills`` into (present in text, absent from text)."""
    haystack = text or ""
    matched: list[str] = []
    missing: list[str] = []
    for skill in skills:
        terms = _expand_skill_terms(skill, aliases)
        if any(contains_term(haystack, t) for t in terms):
            matched.append(skill)
        else:
            missing.append(skill)
    return matched, missing


def match_preferred_roles(title: str, jd: str, preferred_roles: list[str]) -> list[str]:
    """Preferred role families present in the title (or, failing that, the JD)."""
    hits = [r for r in preferred_roles if contains_term(title or "", r)]
    if hits:
        return hits
    return [r for r in preferred_roles if contains_term(jd or "", r)]


def match_excluded(title: str, jd: str, excluded: list[str]) -> list[str]:
    blob = f"{title or ''}\n{jd or ''}"
    return [k for k in excluded if contains_term(blob, k)]


def match_city(
    city: str | None, jd: str, target_cities: list[str], remote_ok: bool
) -> tuple[bool, str]:
    """Return (matched, human-readable reason)."""
    if city:
        for target in target_cities:
            if target and target in city:
                return True, f"城市 {city} 在目标城市列表内"
    if remote_ok and re.search(
        r"远程|remote|在家办公|wfh", f"{city or ''} {jd or ''}", re.IGNORECASE
    ):
        return True, "岗位支持远程办公"
    if not city:
        return False, "JD 未提供城市信息"
    return False, f"城市 {city} 不在目标城市列表内"


# --------------------------------------------------------------------------
# the feature block handed to the model
# --------------------------------------------------------------------------


@dataclass(slots=True)
class PreAnalysis:
    """Deterministic facts about a job, computed before any LLM call."""

    city: str | None
    city_match: bool
    city_reason: str
    matched_roles: list[str] = field(default_factory=list)
    title_family_match: bool = False
    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    skill_overlap_count: int = 0
    skill_overlap_ratio: float = 0.0
    resume_skill_overlap: list[str] = field(default_factory=list)
    excluded_hits: list[str] = field(default_factory=list)
    excluded_softened: bool = False
    experience_required: str = "未明确"
    experience_min_years: int | None = None
    experience_max_years: int | None = None
    experience_is_hard: bool = False
    experience_within_preference: bool | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_meets_minimum: bool | None = None
    heuristic_score: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clamp(value: int, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, value))


def compute_pre_analysis(
    *,
    title: str,
    company: str,
    city: str | None,
    salary_text: str | None,
    experience_text: str | None,
    normalized_description: str,
    strategy: dict[str, Any],
    resume_skills: list[str] | None = None,
) -> PreAnalysis:
    """Compute every deterministic signal the agent will reason over."""
    aliases: dict[str, Any] = strategy.get("skill_aliases") or {}
    relevant_skills: list[str] = list(strategy.get("relevant_skills") or [])
    jd = normalized_description or ""
    jd_and_title = f"{title}\n{jd}"

    matched_skills, missing_skills = match_skills(jd_and_title, relevant_skills, aliases)
    overlap = len(matched_skills)
    ratio = round(overlap / len(relevant_skills), 3) if relevant_skills else 0.0

    resume_overlap: list[str] = []
    if resume_skills:
        resume_blob = " ".join(resume_skills)
        resume_overlap = [
            s
            for s in matched_skills
            if any(contains_term(resume_blob, t) for t in _expand_skill_terms(s, aliases))
        ]

    matched_roles = match_preferred_roles(title, jd, list(strategy.get("preferred_roles") or []))
    excluded_hits = match_excluded(title, jd, list(strategy.get("excluded_keywords") or []))

    override_skills = [s.lower() for s in (strategy.get("excluded_soft_override_skills") or [])]
    scoring_cfg = strategy.get("scoring") or {}
    min_overlap_override = int(scoring_cfg.get("min_skill_overlap_for_override", 3))
    softened = bool(
        excluded_hits
        and overlap >= min_overlap_override
        and any(s.lower() in override_skills for s in matched_skills)
    )

    city_match, city_reason = match_city(
        city, jd, list(strategy.get("target_cities") or []), bool(strategy.get("remote_ok", True))
    )

    exp = extract_experience_requirement(experience_text or "", jd)
    policy = strategy.get("experience_policy") or {}
    pref_max = policy.get("preferred_max_years")
    within: bool | None = None
    if exp.unlimited:
        within = True
    elif exp.min_years is not None and pref_max is not None:
        within = exp.min_years <= int(pref_max)

    salary_min, salary_max = extract_salary_range(salary_text)
    salary_cfg = strategy.get("salary") or {}
    min_monthly = salary_cfg.get("min_monthly_cny")
    salary_ok: bool | None = None
    if salary_max is not None and min_monthly:
        salary_ok = salary_max >= int(min_monthly)

    pre = PreAnalysis(
        city=city,
        city_match=city_match,
        city_reason=city_reason,
        matched_roles=matched_roles,
        title_family_match=bool(matched_roles),
        matched_skills=matched_skills,
        missing_skills=missing_skills,
        skill_overlap_count=overlap,
        skill_overlap_ratio=ratio,
        resume_skill_overlap=resume_overlap,
        excluded_hits=excluded_hits,
        excluded_softened=softened,
        experience_required=exp.describe(),
        experience_min_years=exp.min_years,
        experience_max_years=exp.max_years,
        experience_is_hard=exp.is_hard_requirement,
        experience_within_preference=within,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_meets_minimum=salary_ok,
    )
    pre.heuristic_score = heuristic_score(pre, strategy)
    return pre


def heuristic_score(pre: PreAnalysis, strategy: dict[str, Any]) -> int:
    """A transparent, LLM-free baseline score.

    Shown to the model as a reference point and surfaced in the UI so the user
    can see how far the model moved from the mechanical signal.

    Weights are chosen so a flawless mechanical match lands around 95, not 100:
    a baseline that pegs the top of the scale is useless as a comparison point,
    and the semantic judgement is what earns the last few points.
    """
    score = 35

    if pre.title_family_match:
        score += 15
    if pre.city_match:
        score += 10
    else:
        score -= 10

    ratio = pre.skill_overlap_ratio
    score += int(round(min(ratio, 0.6) / 0.6 * 25))
    if pre.resume_skill_overlap:
        score += min(len(pre.resume_skill_overlap), 4)

    if pre.excluded_hits and not pre.excluded_softened:
        score -= 30
    elif pre.excluded_hits:
        score -= 8

    if pre.experience_within_preference is False:
        # A stretch, not a rejection - the LLM decides how much it matters.
        score -= 12 if pre.experience_is_hard else 6
    elif pre.experience_within_preference is True:
        score += 4

    if pre.salary_meets_minimum is False:
        score -= 5
    elif pre.salary_meets_minimum is True:
        score += 2

    return _clamp(score)


# --------------------------------------------------------------------------
# post-analysis guardrails
# --------------------------------------------------------------------------

VERDICT_ORDER = ("skip", "maybe", "apply", "strong_apply")


def verdict_for_score(score: int, strategy: dict[str, Any]) -> str:
    bands = (strategy.get("scoring") or {}).get("bands") or {}
    if score >= int(bands.get("strong_apply", 90)):
        return "strong_apply"
    if score >= int(bands.get("apply", 80)):
        return "apply"
    if score >= int(bands.get("apply_with_gaps", 70)):
        return "apply"
    if score >= int(bands.get("maybe", 60)):
        return "maybe"
    return "skip"


def apply_guardrails(
    result: dict[str, Any], pre: PreAnalysis, strategy: dict[str, Any]
) -> dict[str, Any]:
    """Clamp model output and enforce the one hard strategy rule.

    * every ``*_score`` is clamped to 0-100;
    * an excluded role with essentially no relevant skill overlap is capped
      (``scoring.excluded_role_score_cap``) and forced to ``skip``;
    * the verdict is only rewritten when it contradicts the (possibly capped)
      score by two full bands, so a deliberate model call still survives.
    """
    out = dict(result)
    scoring_cfg = strategy.get("scoring") or {}

    for key in (
        "overall_score",
        "role_fit_score",
        "skill_fit_score",
        "experience_fit_score",
        "location_fit_score",
    ):
        out[key] = _clamp(int(out.get(key) or 0))
    if out.get("salary_fit_score") is not None:
        out["salary_fit_score"] = _clamp(int(out["salary_fit_score"]))

    risk_flags = list(out.get("risk_flags") or [])

    if pre.excluded_hits and not pre.excluded_softened:
        cap = int(scoring_cfg.get("excluded_role_score_cap", 45))
        if out["overall_score"] > cap:
            hits = "、".join(pre.excluded_hits[:3])
            risk_flags.append(
                f"命中排除关键词（{hits}）且技能重合度不足，总分已按求职策略上限 {cap} 分处理"
            )
            out["overall_score"] = cap
        out["verdict"] = "skip"
    else:
        expected = verdict_for_score(out["overall_score"], strategy)
        given = out.get("verdict")
        if given not in VERDICT_ORDER:
            out["verdict"] = expected
        else:
            gap = abs(VERDICT_ORDER.index(given) - VERDICT_ORDER.index(expected))
            if gap >= 2:
                risk_flags.append(
                    f"模型结论 {given} 与总分 {out['overall_score']} 明显不一致，已按分数校正"
                )
                out["verdict"] = expected

    out["risk_flags"] = risk_flags
    return out
