"""Which search directions actually suit this résumé.

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

The character-overlap fit is a weak stand-in and says so: `云计算工程师`,
`云运维工程师` and `云平台工程师` all scored an identical 33% on one real
résumé, and every point of it came from the shared suffix 工程师. It measures
"this is an engineering role", not "this résumé supports this direction".

**AI fit** - optional, and the reason this module is no longer zero-AI. When a
cached `ResumeDirectionAnalysis` is passed in, its 0-100 judgement replaces the
character overlap. It is *passed in*, never fetched: this module makes no model
call, so ranking stays free and reproducible, and spending money remains the
caller's explicit, confirmed decision.

Evidence still outranks fit either way. A model reading a résumé is a better
guess than counting bigrams, but it is still a guess about what BOSS will
return; a direction that has already surfaced 28 jobs has told us the answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import JobSearchTask, Resume
from app.models.enums import SearchTaskRunStatus
from app.schemas.direction import ResumeDirectionAnalysis
from app.services import search_keyword_analytics as keyword_analytics
from app.services.statistics import Confidence

#: Tokens shared by nearly every Chinese engineering job title. Left in, they
#: dominate the overlap: 云计算工程师 / 云运维工程师 / 云平台工程师 each scored
#: an identical 33% on a real résumé purely because all three end in 工程师,
#: which says nothing about whether that résumé fits any of them.
_GENERIC_ROLE_TOKENS = frozenset(
    {"工程", "程师", "工程师", "开发工程师", "技术", "高级", "资深", "初级", "专家",
     "engineer", "senior", "junior", "staff", "specialist"}
)

#: BOSS's search is a Chinese UI: an English direction matches far fewer
#: postings there. This is a ranking nudge, not an exclusion - an English
#: direction with real evidence still outranks a Chinese one without.
_CHINESE_BONUS = 0.15

#: A direction whose every surfaced job was rejected is not worth searching
#: again while that remains the only thing we know about it.
_PROVEN_EMPTY_PENALTY = 1.0

#: A direction whose completed searches have never rendered a single card is
#: not a weak direction - it is a keyword BOSS returns nothing for, and every
#: run spends one of sixteen units discovering that again. Five of sixteen
#: units did exactly that on 2026-09-05. Large enough to sink such a keyword
#: below every direction that has ever shown a card, and it needs at least two
#: completed runs to say so, because one run can end early for its own reasons.
_BARREN_PENALTY = 10.0
_BARREN_MIN_RUNS = 2


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
    #: 'ai' when a model judged this résumé against this direction, 'text' when
    #: it is the character-overlap fallback. Shown to the user, because the two
    #: are not equally trustworthy.
    fit_source: str = "text"
    #: True when the direction came from the model rather than the strategy
    #: file. Used for this search only - `career_strategy.yaml` is never
    #: auto-edited (CLAUDE.md).
    suggested: bool = False
    #: None when this direction has never been searched.
    recommend_rate: float | None = None
    jobs: int = 0
    recommended: int = 0
    confidence: Confidence = Confidence.insufficient
    reasons: list[str] = field(default_factory=list)
    #: Repeatedly searched and never rendered a card - BOSS returns nothing for
    #: this string. Dropped from the plan, not merely ranked last, and reported
    #: so the console can say why it is gone.
    barren: bool = False

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
        """The directions to actually search, best first.

        A keyword BOSS has repeatedly returned nothing for is dropped rather
        than merely ranked last: with sixteen units to spend, four dead ones
        are a quarter of the run. Fourteen searches that return jobs beat
        sixteen where four return none. Demotion alone was not enough - the
        list of candidates is short enough that a demoted keyword still gets
        picked. If that would leave nothing at all, the demotion order stands
        and the caller gets a plan rather than an empty one.
        """

        alive = [d for d in self.directions if not d.barren]
        return [d.keyword for d in (alive or self.directions)[:limit]]


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


def _text_fit(role: str, vocabulary: set[str]) -> float:
    """Character overlap, with generic occupational tokens removed.

    Without the stoplist every `…工程师` direction scores the same, which reads
    like a measurement and is really just the suffix.
    """
    role_tokens = _tokens(role) - _GENERIC_ROLE_TOKENS
    if not role_tokens:
        return 0.0
    return len(role_tokens & vocabulary) / len(role_tokens)


def rank(
    db: Session,
    *,
    resume: Resume | None,
    strategy: dict,
    ai: ResumeDirectionAnalysis | None = None,
) -> DirectionRanking:
    """Order the directions by evidence first, then résumé fit.

    `ai` is an already-cached analysis or None. This function never fetches
    one: paying for it is the caller's confirmed decision, so ranking itself
    stays free.
    """
    roles = [str(r).strip() for r in (strategy.get("preferred_roles") or []) if str(r).strip()]
    ai_fits: dict[str, tuple[int, str]] = {}
    suggested_roles: list[str] = []
    if ai is not None:
        for item in ai.directions:
            ai_fits[item.keyword.strip()] = (item.fit, item.reason.strip())
        for item in ai.suggested:
            keyword = item.keyword.strip()
            if keyword and keyword not in roles and keyword not in ai_fits:
                ai_fits[keyword] = (item.fit, item.reason.strip())
                suggested_roles.append(keyword)

    if not roles and not suggested_roles:
        return DirectionRanking(
            notes=["职业策略里没有岗位方向，请先在设置中添加。"], needs_more_evidence=True
        )

    vocabulary = _resume_vocabulary(resume, strategy)
    analytics = keyword_analytics.compute(db)
    cohorts = {c.keyword: c for c in analytics.cohorts}
    barren = _barren_keywords(db)

    scored: list[DirectionScore] = []
    for role in dict.fromkeys([*roles, *suggested_roles]):
        reasons: list[str] = []
        judged = ai_fits.get(role)
        if judged is not None:
            fit = judged[0] / 100
            fit_source = "ai"
            reasons.append(f"AI 判断简历支撑度 {judged[0]}/100：{judged[1]}")
        elif ai is not None:
            # An AI analysis exists but skipped this direction (the prompt says
            # to cover every one, so this is a model defect). A character count
            # is not a judgement and must not be ranked against one: 云运维工程师
            # can score a perfect character overlap purely by appearing verbatim
            # in the résumé, which would beat a direction the model actually
            # assessed at 95/100. No comparable measurement, so none is invented.
            fit = 0.0
            fit_source = "unjudged"
            reasons.append("AI 未覆盖此方向，没有可比较的匹配度")
        else:
            fit = _text_fit(role, vocabulary)
            fit_source = "text"
            if fit > 0:
                reasons.append(f"与简历用词重合度 {fit:.0%}（未经 AI 判断）")
        score = fit
        if role in suggested_roles:
            reasons.append("AI 依据简历补充的方向，未写入职业策略")

        if _is_chinese(role):
            score += _CHINESE_BONUS
        else:
            reasons.append("英文方向在 BOSS 上命中较少")

        runs = barren.get(role)
        if runs:
            score -= _BARREN_PENALTY
            reasons.append(f"过去 {runs} 次搜索一张卡片都没返回，已停止使用")

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
                fit_source=fit_source,
                suggested=role in suggested_roles,
                recommend_rate=rate,
                jobs=jobs or 0,
                recommended=recommended or 0,
                confidence=confidence,
                reasons=reasons,
                barren=bool(runs),
            )
        )

    # Highest score first; ties break on evidence, then on the configured order
    # so the result is stable rather than arbitrary.
    order = {role: i for i, role in enumerate(dict.fromkeys([*roles, *suggested_roles]))}
    scored.sort(key=lambda d: (-d.score, not d.has_evidence, order[d.keyword]))

    evidenced = sum(1 for d in scored if d.has_evidence)
    ranking = DirectionRanking(directions=scored, needs_more_evidence=evidenced < 2)
    ranking.notes = _build_notes(ranking, evidenced)
    return ranking


def _barren_keywords(db: Session) -> dict[str, int]:
    """Keywords whose completed searches have never rendered a card.

    Deterministic, from the user's own run history - no model call. A keyword
    BOSS returns nothing for is not the same as a keyword that returns poor
    matches: the second is a judgement, the first is a fact the search itself
    already established, twice.
    """

    rows = db.execute(
        select(
            JobSearchTask.keywords,
            func.count(JobSearchTask.id),
            func.coalesce(func.sum(JobSearchTask.observed_count), 0),
        )
        .where(
            JobSearchTask.run_status == SearchTaskRunStatus.completed,
            JobSearchTask.keywords.is_not(None),
        )
        .group_by(JobSearchTask.keywords)
    ).all()
    return {
        keyword: runs
        for keyword, runs, observed in rows
        if observed == 0 and runs >= _BARREN_MIN_RUNS
    }


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
    suggested = [d.keyword for d in ranking.directions if d.suggested]
    if suggested:
        notes.append(
            "AI 依据简历补充了这些方向：" + "、".join(suggested)
            + "。本次搜索会用到，但不会自动写进职业策略。"
        )
    if ranking.needs_more_evidence:
        by_ai = any(d.fit_source == "ai" for d in ranking.directions)
        basis = "AI 对简历的判断" if by_ai else "简历用词重合度（未经 AI 判断，参考价值有限）"
        notes.append(
            f"目前只有 {evidenced} 个方向有足够的历史样本，排序主要依据{basis}；"
            "多搜几轮后这个排序会更可靠。"
        )
    return notes
