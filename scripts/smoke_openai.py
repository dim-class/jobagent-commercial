"""One-shot OpenAI connectivity check for JobMatchAgent.

Makes exactly ONE real API call, using the fast (bulk) model, with a tiny
synthetic job so the cost is negligible. Nothing is written to the database.

    python scripts\\smoke_openai.py
    python scripts\\smoke_openai.py --smart

Exit codes:
    0  success, or SKIPPED because no key is configured
    1  the call failed

Without OPENAI_API_KEY this prints "SKIPPED - OPENAI_API_KEY not configured"
and exits 0. It is deliberately NOT part of the pytest suite: automated tests
must never spend API money.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.agents.job_match_agent import run_job_match  # noqa: E402
from app.core.career_strategy import load_strategy  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.logging import ensure_utf8_stdout, setup_logging  # noqa: E402
from app.services.job_normalizer import normalize_job  # noqa: E402
from app.services.scoring import compute_pre_analysis  # noqa: E402

SAMPLE_RESUME_PROFILE = {
    "summary": "3 年云计算与系统运维经验，熟悉 AWS 与 Linux 自动化运维。",
    "years_of_experience": 3,
    "skills": ["AWS", "Linux", "Docker", "Terraform", "Python", "Shell"],
    "certifications": ["AWS Certified Solutions Architect - Associate"],
    "work_experience": ["负责 AWS EC2/VPC/ALB 日常运维与成本优化", "使用 Terraform 管理基础设施"],
    "projects": ["核心系统上云：将 20 套应用迁移至 AWS"],
    "education": ["计算机科学与技术 本科"],
}

SAMPLE_RESUME_TEXT = (
    "云运维工程师，3 年经验。负责 AWS 云平台（EC2、VPC、ALB、S3）的日常运维，"
    "使用 Terraform 管理基础设施即代码，编写 Python 与 Shell 自动化脚本，"
    "基于 Prometheus 与 Grafana 搭建监控告警。"
)

SAMPLE_JD = """岗位职责：
1. 负责 AWS 云平台的日常运维与优化；
2. 使用 Terraform 维护基础设施即代码；
3. 维护 Docker 与 Kubernetes 集群；
4. 编写 Python / Shell 自动化脚本。

任职要求：
1. 1-3 年云计算或运维经验；
2. 熟悉 Linux 与网络基础；
3. 有 AWS 使用经验者优先。
"""


async def _run(model: str) -> int:
    strategy = load_strategy(force=True)
    job = normalize_job(
        company="示例科技（冒烟测试）",
        title="云计算工程师",
        raw_description=SAMPLE_JD,
        city="北京",
        salary_text="20k-30k",
        experience_text="1-3年",
    )
    pre = compute_pre_analysis(
        title=job.title,
        company=job.company,
        city=job.city,
        salary_text=job.salary_text,
        experience_text=job.experience_text,
        normalized_description=job.normalized_description,
        strategy=strategy,
        resume_skills=list(SAMPLE_RESUME_PROFILE["skills"]),
    )

    print(f"  model            {model}")
    print(f"  heuristic score  {pre.heuristic_score}")
    print("  calling OpenAI ...")

    started = time.perf_counter()
    result = await run_job_match(
        model_name=model,
        resume_profile=SAMPLE_RESUME_PROFILE,
        resume_excerpt=SAMPLE_RESUME_TEXT,
        strategy=strategy,
        job={
            "company": job.company,
            "title": job.title,
            "city": job.city,
            "salary_text": job.salary_text,
            "experience_text": job.experience_text,
            "education_text": None,
            "normalized_description": job.normalized_description,
        },
        pre_analysis=pre.to_dict(),
    )
    elapsed = time.perf_counter() - started

    print()
    print(f"  PASSED in {elapsed:.1f}s")
    print(f"  overall_score    {result.overall_score}")
    print(f"  verdict          {result.verdict.value}")
    print(f"  matched_skills   {', '.join(result.matched_skills) or '-'}")
    print(f"  reasoning        {result.reasoning_summary}")
    print(f"  greeting ({len(result.greeting_message)} chars)")
    print(f"    {result.greeting_message}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenAI smoke test for JobMatchAgent")
    parser.add_argument(
        "--smart", action="store_true", help="use OPENAI_MODEL_SMART instead of OPENAI_MODEL_FAST"
    )
    args = parser.parse_args()

    ensure_utf8_stdout()
    setup_logging("INFO")
    settings = get_settings()

    print("JobMatchAgent OpenAI smoke test")
    if not settings.openai_configured:
        print("SKIPPED - OPENAI_API_KEY not configured")
        print("  Set it in the project-root .env file to enable this check.")
        return 0

    model = settings.openai_model_smart if args.smart else settings.openai_model_fast
    try:
        return asyncio.run(_run(model))
    except Exception as exc:  # noqa: BLE001
        message = getattr(exc, "message", None) or str(exc)
        print()
        print(f"FAILED - {type(exc).__name__}: {message}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
