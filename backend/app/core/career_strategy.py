"""Load / save the editable career strategy (``config/career_strategy.yaml``).

The strategy is *data*, never code. It is cached in-process and reloaded when
the file's mtime changes, so editing the YAML by hand takes effect on the next
request without a restart.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import yaml

from app.core.config import settings
from app.core.errors import ValidationError
from app.core.logging import get_logger, log_event
from app.services.hashing import hash_json

logger = get_logger(__name__)

_lock = threading.Lock()
_cache: dict[str, Any] | None = None
_cache_mtime: float | None = None

DEFAULT_STRATEGY: dict[str, Any] = {
    "version": 1,
    "target_cities": ["北京", "上海", "广州", "杭州"],
    "remote_ok": True,
    "preferred_roles": [
        "Cloud Engineer",
        "DevOps Engineer",
        "SRE",
        "Platform Engineer",
        "云计算工程师",
    ],
    "relevant_skills": ["AWS", "Linux", "Docker", "Kubernetes", "Python"],
    "skill_aliases": {},
    "excluded_keywords": ["Helpdesk", "Desktop Support", "桌面运维", "销售"],
    "excluded_soft_override_skills": ["AWS", "Kubernetes", "Terraform"],
    "experience_policy": {
        "preferred_min_years": 1,
        "preferred_max_years": 3,
        "hard_reject_above_years": 8,
        "flexibility": "balanced",
        "notes": "",
    },
    "salary": {"min_monthly_cny": 15000, "ideal_monthly_cny": 25000, "hard_filter": False},
    "scoring": {
        "bands": {"strong_apply": 90, "apply": 80, "apply_with_gaps": 70, "maybe": 60},
        "excluded_role_score_cap": 45,
        "min_skill_overlap_for_override": 3,
    },
    "preferences": {"notes": ""},
}

_REQUIRED_LIST_KEYS = ("target_cities", "preferred_roles", "relevant_skills", "excluded_keywords")


def strategy_path() -> Path:
    return settings.strategy_file


def _validate(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValidationError("career strategy must be a YAML mapping")
    merged = {**DEFAULT_STRATEGY, **data}
    for key in _REQUIRED_LIST_KEYS:
        value = merged.get(key)
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValidationError(f"career strategy field '{key}' must be a list of strings")
    if not isinstance(merged.get("skill_aliases", {}), dict):
        raise ValidationError("career strategy field 'skill_aliases' must be a mapping")
    # Nested dicts get merged one level deep so a partial PUT keeps defaults.
    for key in ("experience_policy", "salary", "scoring", "preferences"):
        base = DEFAULT_STRATEGY[key]
        incoming = merged.get(key) or {}
        if not isinstance(incoming, dict):
            raise ValidationError(f"career strategy field '{key}' must be a mapping")
        merged[key] = {**base, **incoming}
    return merged


def load_strategy(*, force: bool = False) -> dict[str, Any]:
    """Return the strategy dict, reloading it if the file changed on disk."""
    global _cache, _cache_mtime
    path = strategy_path()
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        log_event(logger, "strategy.missing_file_using_defaults", path=str(path))
        return dict(DEFAULT_STRATEGY)

    with _lock:
        if not force and _cache is not None and _cache_mtime == mtime:
            return _cache
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        data = _validate(raw)
        _cache, _cache_mtime = data, mtime
        log_event(logger, "strategy.loaded", cities=len(data["target_cities"]), hash=strategy_hash(data)[:12])
        return data


def save_strategy(data: dict[str, Any]) -> dict[str, Any]:
    """Validate and persist the strategy back to YAML."""
    global _cache, _cache_mtime
    validated = _validate(data)
    path = strategy_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(
        validated, allow_unicode=True, sort_keys=False, default_flow_style=False, width=100
    )
    header = (
        "# 求职策略 / Career strategy\n"
        "# Written by the app (设置 -> 求职策略). Hand-editing is fine; the backend\n"
        "# reloads this file whenever its mtime changes.\n"
    )
    path.write_text(header + body, encoding="utf-8")
    with _lock:
        _cache, _cache_mtime = validated, path.stat().st_mtime
    log_event(logger, "strategy.saved", hash=strategy_hash(validated)[:12])
    return validated


def strategy_hash(data: dict[str, Any] | None = None) -> str:
    """Stable hash of the strategy - part of the analysis cache key."""
    return hash_json(data if data is not None else load_strategy())
