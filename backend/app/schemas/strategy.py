"""Career-strategy schemas.

The strategy is intentionally loose (a YAML document the user owns), so the
API validates the fields the app depends on and passes the rest through
untouched instead of forcing the user's file into a rigid model.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ExperiencePolicy(BaseModel):
    model_config = ConfigDict(extra="allow")

    preferred_min_years: int = 1
    preferred_max_years: int = 3
    hard_reject_above_years: int = 8
    flexibility: str = "balanced"
    notes: str = ""


class SalaryPolicy(BaseModel):
    model_config = ConfigDict(extra="allow")

    min_monthly_cny: int = 15000
    ideal_monthly_cny: int = 25000
    hard_filter: bool = False


class CareerStrategy(BaseModel):
    """What the UI edits. Unknown keys survive a round-trip."""

    model_config = ConfigDict(extra="allow")

    version: int = 1
    target_cities: list[str] = Field(default_factory=list)
    remote_ok: bool = True
    preferred_roles: list[str] = Field(default_factory=list)
    relevant_skills: list[str] = Field(default_factory=list)
    skill_aliases: dict[str, Any] = Field(default_factory=dict)
    excluded_keywords: list[str] = Field(default_factory=list)
    excluded_soft_override_skills: list[str] = Field(default_factory=list)
    experience_policy: ExperiencePolicy = Field(default_factory=ExperiencePolicy)
    salary: SalaryPolicy = Field(default_factory=SalaryPolicy)
    scoring: dict[str, Any] = Field(default_factory=dict)
    preferences: dict[str, Any] = Field(default_factory=dict)


class CareerStrategyResponse(BaseModel):
    strategy: CareerStrategy
    path: str
    hash: str
