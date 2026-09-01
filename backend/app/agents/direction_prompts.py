"""Prompt for the résumé -> search-direction agent.

Bump :data:`DIRECTION_PROMPT_VERSION` on any wording change: it is part of the
cache key, so a changed prompt re-analyses instead of serving a stale verdict.
"""

from __future__ import annotations

import json
from typing import Any

#: Part of the cache key. Bump on every prompt change.
DIRECTION_PROMPT_VERSION = "direction-v1"

#: How much raw résumé text to send alongside the parsed profile.
RESUME_EXCERPT_CHARS = 6000
#: At most this many new keywords may be proposed.
MAX_SUGGESTIONS = 5

SYSTEM_PROMPT = """你是一位熟悉中国招聘市场（尤其是 BOSS 直聘）的求职顾问。

你的任务：判断这份简历适合用哪些**岗位关键词**在 BOSS 直聘上搜索。
你不是在评价某一个具体岗位，而是在决定"该往哪个方向找"。

评分原则：

1. **只依据简历里真实写到的内容**。简历没写过的经历、技能、行业，一律不得
   假设、不得推断、不得脑补。理由必须能在简历原文里找到出处。
2. **fit 是"简历对这个方向的支撑程度"**，不是"这个方向好不好"。一个热门方向
   如果简历完全没有相关经历，就应该是低分。
3. **区分核心经历与边缘接触**。简历主线做的事给高分；只在某个项目里顺带用过
   一次的技术，给低分并在理由里说明。
4. **中文关键词优先**。BOSS 直聘上的岗位标题绝大多数是中文，英文关键词命中
   很少。如果一个方向只有英文写法，请在 suggested 里给出对应的中文说法。
5. **suggested 只放候选列表里没有、但简历确实支撑的中文关键词**，最多 %(max_suggestions)d 个。
   它应该是 BOSS 上真实存在的岗位名（例如「基础设施工程师」「云平台运维」），
   不是能力描述或自造词。宁可少给，不要凑数。
6. directions 必须**逐个覆盖**给定的候选方向，数量和名称完全一致，不增不减。

年限、学历、语言这些硬性条件不在这一步判断，留给后续的岗位匹配。
""" % {"max_suggestions": MAX_SUGGESTIONS}


def build_user_prompt(
    *,
    resume_profile: dict[str, Any],
    resume_excerpt: str,
    strategy: dict[str, Any],
    candidates: list[str],
    history: dict[str, dict[str, Any]],
) -> str:
    """Assemble the one user turn.

    `history` is included as *context*, not as an instruction: the caller
    combines the model's fit with those numbers itself, so the model does not
    get to launder a small sample into a confident recommendation.
    """
    strategy_view = {
        "preferred_roles": strategy.get("preferred_roles") or [],
        "excluded_roles": strategy.get("excluded_roles") or [],
        "target_cities": strategy.get("target_cities") or [],
    }
    sections = [
        "## 简历结构化信息",
        json.dumps(resume_profile, ensure_ascii=False, indent=2),
        "",
        "## 简历原文节选",
        (resume_excerpt or "")[:RESUME_EXCERPT_CHARS] or "（无）",
        "",
        "## 求职策略（用户自己填的）",
        json.dumps(strategy_view, ensure_ascii=False, indent=2),
        "",
        "## 需要评分的候选方向",
        json.dumps(candidates, ensure_ascii=False, indent=2),
    ]
    if history:
        sections += [
            "",
            "## 这些方向过去搜到的结果（仅供参考，不要据此调整 fit）",
            json.dumps(history, ensure_ascii=False, indent=2),
        ]
    sections += [
        "",
        "请对每个候选方向给出 fit 与理由，并补充简历支撑但列表里没有的中文关键词。",
    ]
    return "\n".join(sections)
