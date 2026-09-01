"""Typed output for the résumé -> search-direction agent.

Separate from `JobMatchResult` on purpose: scoring one JD against a résumé and
deciding *what to search for in the first place* are different questions, and
collapsing them would make one prompt answer both badly.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DirectionFit(BaseModel):
    """One search direction, judged against the résumé."""

    keyword: str = Field(description="BOSS 直聘上可直接搜索的岗位关键词")
    fit: int = Field(ge=0, le=100, description="简历对该方向的支撑程度 0-100")
    reason: str = Field(description="一句话说明依据，只能引用简历里真实写到的内容")


class ResumeDirectionAnalysis(BaseModel):
    """The whole judgement: the configured directions, plus better ideas."""

    directions: list[DirectionFit] = Field(
        description="对每一个给定的候选方向的评分，必须逐个覆盖，不得遗漏或新增"
    )
    suggested: list[DirectionFit] = Field(
        default_factory=list,
        description="简历支持、但候选列表里没有的中文岗位关键词，最多 5 个",
    )
    summary: str = Field(default="", description="一句话总结这份简历的求职定位")
