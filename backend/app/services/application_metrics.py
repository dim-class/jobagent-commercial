"""Deterministic funnel metrics (v0.4).

No LLM is involved and none ever should be: these are counts and ratios over
rows the human created. v0.4 tracks; it does not draw conclusions.

Built to be reused by the v0.6 response-rate analytics, hence the breakdowns
by city / role family / source even though the UI only shows the headline
funnel today.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.career_strategy import load_strategy
from app.models import Job, JobStatus, Verdict
from app.models.enums import RECOMMENDED_VERDICTS
from app.schemas.application import (
    ApplicationMetrics,
    FunnelCounts,
    FunnelRates,
)
from app.services.application_queue import latest_analysis_of, load_jobs

#: Role families for analytics. Order matters - the first hit wins, so the
#: more specific families come before the broad ones.
ROLE_FAMILY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("SRE", ("sre", "site reliability", "站点可靠性", "稳定性工程")),
    ("DevOps", ("devops", "运维开发", "研发效能", "ci/cd", "持续交付")),
    ("Platform", ("platform engineer", "平台工程", "云平台", "平台开发", "paas")),
    ("Cloud", ("cloud", "云计算", "云原生", "云运维", "aws", "阿里云", "公有云")),
    ("Infrastructure", ("infrastructure", "基础架构", "基础设施", "系统运维", "中间件", "网络工程")),
)

OTHER_FAMILY = "Other"

#: Sources we report separately; anything else is folded into "other".
KNOWN_SOURCES: frozenset[str] = frozenset({"manual", "boss", "liepin", "zhaopin", "job51", "demo"})


def role_family(title: str, *, strategy: dict | None = None) -> str:
    """Rough deterministic bucket for a job title.

    Uses the career strategy's preferred-role list as extra vocabulary so a
    user who targets an unusual title still gets a sensible family, and falls
    back to ``Other`` rather than guessing.
    """
    lowered = (title or "").lower()
    if not lowered.strip():
        return OTHER_FAMILY

    for family, keywords in ROLE_FAMILY_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return family

    # Second pass: the user's own target titles from career_strategy.yaml.
    cfg = strategy if strategy is not None else load_strategy()
    for preferred in cfg.get("preferred_roles") or []:
        if preferred and preferred.lower() in lowered:
            for family, keywords in ROLE_FAMILY_KEYWORDS:
                if any(keyword in preferred.lower() for keyword in keywords):
                    return family
    return OTHER_FAMILY


def source_bucket(source: str | None) -> str:
    value = (source or "").strip().lower()
    return value if value in KNOWN_SOURCES else "other"


def _rate(numerator: int, denominator: int) -> float | None:
    """None when the denominator is zero - never a misleading 0.0."""
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _blank_bucket() -> dict[str, int]:
    return {"jobs": 0, "recommended": 0, "applied": 0, "replied": 0, "interview": 0, "offer": 0}


#: Statuses that imply the human already applied, even if they have since moved
#: further down the funnel. Without this, an offer would not count as applied.
_APPLIED_OR_LATER = {
    JobStatus.applied,
    JobStatus.replied,
    JobStatus.interview,
    JobStatus.offer,
    JobStatus.rejected,
}
_REPLIED_OR_LATER = {JobStatus.replied, JobStatus.interview, JobStatus.offer}
_INTERVIEW_OR_LATER = {JobStatus.interview, JobStatus.offer}


def compute_metrics(db: Session) -> ApplicationMetrics:
    jobs: list[Job] = load_jobs(db)
    strategy = load_strategy()

    counts = FunnelCounts(total_jobs=len(jobs))
    by_city: dict[str, dict[str, int]] = {}
    by_family: dict[str, dict[str, int]] = {}
    by_source: dict[str, dict[str, int]] = {}

    for job in jobs:
        analysis = latest_analysis_of(job)
        recommended = analysis is not None and analysis.verdict in RECOMMENDED_VERDICTS

        if analysis is not None:
            counts.analyzed_jobs += 1
        if recommended:
            counts.recommended_jobs += 1

        applied = job.status in _APPLIED_OR_LATER
        replied = job.status in _REPLIED_OR_LATER
        interviewed = job.status in _INTERVIEW_OR_LATER

        if applied:
            counts.applied_jobs += 1
        if replied:
            counts.replied_jobs += 1
        if interviewed:
            counts.interview_jobs += 1
        if job.status is JobStatus.offer:
            counts.offer_jobs += 1
        if job.status is JobStatus.rejected:
            counts.rejected_jobs += 1

        for bucket, key in (
            (by_city, job.city or "未标注"),
            (by_family, role_family(job.title, strategy=strategy)),
            (by_source, source_bucket(job.source)),
        ):
            entry = bucket.setdefault(key, _blank_bucket())
            entry["jobs"] += 1
            entry["recommended"] += int(recommended)
            entry["applied"] += int(applied)
            entry["replied"] += int(replied)
            entry["interview"] += int(interviewed)
            entry["offer"] += int(job.status is JobStatus.offer)

    rates = FunnelRates(
        application_response_rate=_rate(counts.replied_jobs, counts.applied_jobs),
        application_interview_rate=_rate(counts.interview_jobs, counts.applied_jobs),
        response_interview_rate=_rate(counts.interview_jobs, counts.replied_jobs),
    )

    return ApplicationMetrics(
        counts=counts,
        rates=rates,
        by_city=dict(sorted(by_city.items(), key=lambda kv: -kv[1]["jobs"])),
        by_role_family=dict(sorted(by_family.items(), key=lambda kv: -kv[1]["jobs"])),
        by_source=dict(sorted(by_source.items(), key=lambda kv: -kv[1]["jobs"])),
    )


def verdict_breakdown(db: Session) -> dict[str, int]:
    """Recommendation counts - kept separate from human status on purpose."""
    out = {v.value: 0 for v in Verdict}
    for job in load_jobs(db):
        analysis = latest_analysis_of(job)
        if analysis is not None:
            out[analysis.verdict.value] += 1
    return out
