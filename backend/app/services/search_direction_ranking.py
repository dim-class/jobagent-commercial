"""Which search directions actually suit this résumé - deterministic, zero AI.

The comprehensive search used to take the first N entries of
``career_strategy.preferred_roles`` in configured order. That order has nothing
to do with the résumé, so an English direction that BOSS barely matches
(``Cloud Engineer``) could crowd out a Chinese one the user is genuinely
qualified for, and a direction already proven useless (``基础设施``: 8 jobs,
0 recommended) kept being searched.

Two local signals decide instead, and they are combined on purpose:

**Evidence** - what each direction has actually surfaced, from
``search_keyword_analytics``. This is the strongest signal when it exists, but
it only exists for directions that have already been searched.

**Fit** - overlap between the direction's own words and the résumé's parsed
skills plus the strategy's `relevant_skills`. This is what lets a
never-searched direction be ranked at all.

Neither is a model call. Everything here is a count, a ratio or a substring
test over rows the user already has, so re-running it is free and reproducible.

When the pool is thin on both signals the result says so via
``needs_more_evidence``; the caller may then *offer* a paid AI pass. Nothing
here ever makes that call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import Resume
from app.services import search_keyword_analytics as keyword_analytics
from app.services.statistics import Confidence

#: BOSS's search is a Chinese UI: an English direction matches far fewer
#: postings there. This is a ranking nudge, not an exclusion - an English
#: direction with real evidence still outranks a Chinese one without.
_CHINESE_BONUS = 0.15

#: A direction whose every surfaced job was rejected is not worth searching
#: again while that remains the only thing we know about it.
_PROVEN_EMPTY_PENALTY = 1.0


def _is_chinese(text: str) -> bool:
    return any("一" <= char <= "鿿" for char in text)


def _tokens(text: str) -> set[str]:
    """Lowercased word-ish tokens, plus CJK character bigrams.

    CJK has no spaces, so `云计算工程师` and `云平台` share nothing by word
    splitting; bigrams give them `云计`/`云平` etc. to overlap on.
    """
    lowered = (text or "").lower()
    words = {w for w in re.split(r"[^0-9a-z一-鿿]+", lowered) if len(w) > 1}
    cjk = "".join(c for c in lowered if "一" <= c <= "鿿")
    bigrams = {cjk[i : i + 2] for i in range(len(cjk) - 1)}
    return words | bigrams


@dataclass(slots=True)
class DirectionScore:
    """One candidate direction, why it scored, and whether that is evidence."""

    keyword: str
    score: float
    fit: float
    #: None when this direction has never been searched.
    recommend_rate: float | None = None
    jobs: int = 0
    recommended: int = 0
    confidence: Confidence = Confidence.insufficient
    reasons: list[str] = field(default_factory=list)

    @property
    def has_evidence(self) -> bool:
        return self.confidence is not Confidence.insufficient


@dataclass(slots=True)
class DirectionRanking:
    directions: list[DirectionScore] = field(default_factory=list)
    #: True when too few directions carry real outcome evidence for the ranking
    #: to be more than a fit guess. The caller may offer a paid AI pass; this
    #: module never makes one.
    needs_more_evidence: bool = False
    notes: list[str] = field(default_factory=list)

    def top(self, limit: int) -> list[str]:
        return [d.keyword for d in self.directions[:limit]]


def _resume_vocabulary(resume: Resume | None, strategy: dict) -> set[str]:
    """Everything local that describes what this person actually does."""
    vocabulary: set[str] = set()
    profile = (resume.parsed_profile_json if resume else None) or {}
    # Skills are usually written in English (AWS, Kubernetes) while BOSS role
    # names are Chinese, so skills alone overlap with nothing. The narrative
    # sections are where this resume actually says 基础设施工程师 / 云迁移 /
    # 运维 - that is what a Chinese direction can match on.
    for key in (
        "skills",
        "highlighted_skills",
        "certifications",
        "summary",
        "work_experience",
        "projects",
    ):
        entry = profile.get(key) or []
        values = [entry] if isinstance(entry, str) else entry
        for value in values:
            vocabulary |= _tokens(str(value))
    for key in ("relevant_skills", "skill_aliases"):
        entry = strategy.get(key) or []
        values = entry.keys() if isinstance(entry, dict) else entry
        for value in values:
            vocabulary |= _tokens(str(value))
    return vocabulary


def rank(
    db: Session,
    *,
    resume: Resume | None,
    strategy: dict,
) -> DirectionRanking:
    """Order the strategy's directions by evidence first, then résumé fit."""
    roles = [str(r).strip() for r in (strategy.get("preferred_roles") or []) if str(r).strip()]
    if not roles:
        return DirectionRanking(
            notes=["职业策略里没有岗位方向，请先在设置中添加。"], needs_more_evidence=True
        )

    vocabulary = _resume_vocabulary(resume, strategy)
    analytics = keyword_analytics.compute(db)
    cohorts = {c.keyword: c for c in analytics.cohorts}

    scored: list[DirectionScore] = []
    for role in dict.fromkeys(roles):
        reasons: list[str] = []
        role_tokens = _tokens(role)
        fit = (
            len(role_tokens & vocabulary) / len(role_tokens) if role_tokens else 0.0
        )
        score = fit
        if fit > 0:
            reasons.append(f"与简历技能重合度 {fit:.0%}")

        if _is_chinese(role):
            score += _CHINESE_BONUS
        else:
            reasons.append("英文方向在 BOSS 上命中较少")

        cohort = cohorts.get(role)
        rate = jobs = recommended = None
        confidence = Confidence.insufficient
        if cohort is not None:
            jobs, recommended = cohort.jobs, cohort.recommended
            rate, confidence = cohort.recommend_rate, cohort.confidence
            if confidence is not Confidence.insufficient and rate is not None:
                # Real outcome evidence outweighs a text-overlap guess.
                score += rate * 2
                reasons.append(f"历史推荐率 {recommended}/{jobs}（{rate:.0%}）")
                if recommended == 0:
                    score -= _PROVEN_EMPTY_PENALTY
                    reasons.append("此前没有一个岗位被判定为推荐投递")
            elif jobs:
                reasons.append(f"已搜过 {jobs} 个岗位，样本不足以下结论")

        scored.append(
            DirectionScore(
                keyword=role,
                score=round(score, 4),
                fit=round(fit, 4),
                recommend_rate=rate,
                jobs=jobs or 0,
                recommended=recommended or 0,
                confidence=confidence,
                reasons=reasons,
            )
        )

    # Highest score first; ties break on evidence, then on the configured order
    # so the result is stable rather than arbitrary.
    order = {role: i for i, role in enumerate(dict.fromkeys(roles))}
    scored.sort(key=lambda d: (-d.score, not d.has_evidence, order[d.keyword]))

    evidenced = sum(1 for d in scored if d.has_evidence)
    ranking = DirectionRanking(directions=scored, needs_more_evidence=evidenced < 2)
    ranking.notes = _build_notes(ranking, evidenced)
    return ranking


def _build_notes(ranking: DirectionRanking, evidenced: int) -> list[str]:
    """Template sentences, so the explanation can be re-derived and checked."""
    notes: list[str] = []
    if not ranking.directions:
        return notes
    best = ranking.directions[0]
    notes.append(
        f"优先搜索「{best.keyword}」"
        + (f"：{'；'.join(best.reasons)}。" if best.reasons else "。")
    )
    dropped = [d for d in ranking.directions if d.recommended == 0 and d.has_evidence]
    for direction in dropped:
        notes.append(
            f"「{direction.keyword}」已搜过 {direction.jobs} 个岗位且无一推荐，已排到最后。"
        )
    if ranking.needs_more_evidence:
        notes.append(
            f"目前只有 {evidenced} 个方向有足够的历史样本，排序主要依据简历技能重合度；"
            "多搜几轮后这个排序会更可靠。"
        )
    return notes
