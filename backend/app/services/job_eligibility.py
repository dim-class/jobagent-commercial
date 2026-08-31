"""Deterministic candidate eligibility rules for the user's search runner.

This module deliberately contains no model call.  It is applied both before an
extension import and at the import boundary so the automatic runner cannot
bypass it.  Manual JobAgent intake remains available for historical records.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.errors import ValidationError


@dataclass(frozen=True, slots=True)
class EligibilityDecision:
    eligible: bool
    reason: str | None = None


_TITLE_MARKERS = (
    "应届",
    "校招",
    "校园招聘",
    "毕业生",
    "管培生",
    "实习",
    "实习生",
)
_COHORT_RE = re.compile(r"(?:20)?2[4-9]\s*届")
_DESCRIPTION_PATTERNS = (
    re.compile(r"(?:仅限|只招|面向|招聘)\s*(?:20\d{2}\s*届\s*)?应届"),
    re.compile(r"(?:校园招聘|校招岗位|应届毕业生|在校生|实习生)"),
)
_NEGATIONS = ("不限应届", "非应届", "有经验者", "社招")


def classify_non_experienced_track(title: str | None, description: str | None) -> EligibilityDecision:
    """Reject explicit campus/fresh-graduate/intern tracks, conservatively.

    Title markers are authoritative.  Description-only matches require an
    explicit campus/fresh-graduate phrase and are ignored when the surrounding
    posting clearly says it is not restricted to fresh graduates.
    """
    normalized_title = " ".join((title or "").split())
    normalized_description = " ".join((description or "").split())
    if any(marker in normalized_title for marker in _TITLE_MARKERS) or _COHORT_RE.search(normalized_title):
        return EligibilityDecision(False, "已排除应届生、校招或实习岗位。")
    if any(negation in normalized_description for negation in _NEGATIONS):
        return EligibilityDecision(True)
    if any(pattern.search(normalized_description) for pattern in _DESCRIPTION_PATTERNS):
        return EligibilityDecision(False, "已排除应届生、校招或实习岗位。")
    return EligibilityDecision(True)


def is_early_career_track(title: str | None, description: str | None) -> bool:
    """Return whether the posting explicitly targets an early-career cohort."""
    return not classify_non_experienced_track(title, description).eligible


def evaluate_early_career_policy(
    title: str | None,
    description: str | None,
    policy: str,
) -> EligibilityDecision:
    """Apply one snapshotted candidate-stage policy without a model call."""
    if policy == "include":
        return EligibilityDecision(True)
    if policy == "exclude":
        return classify_non_experienced_track(title, description)
    if policy == "only":
        if is_early_career_track(title, description):
            return EligibilityDecision(True)
        return EligibilityDecision(False, "当前设置只保留应届、校招或实习岗位。")
    raise ValidationError(
        "未知的候选阶段设置。", detail={"field": "early_career_policy"}
    )
