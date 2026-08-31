"""BOSS city name -> structured city-code mapping (M4e, explicitly authorized).

A local, hardcoded lookup table only - never fetched from BOSS's own city
endpoint (CLAUDE.md forbids that: no ``/wapi/`` calls, no private API). The
four nine-digit codes below are the widely-documented national
administrative-division city IDs BOSS's own search URL already uses (the
same convention this repo's extension test fixtures already exercise, e.g.
``city=101020100`` for Shanghai in ``backend/tests/test_extension_extraction.py``)
- not fetched or scraped, and not yet independently re-confirmed against a
live BOSS search for every city here beyond that pre-existing fixture
evidence. Live confirmation remains the compatibility authority, exactly as
for every other not-yet-fully-verified value in this codebase.

An unknown city name fails closed and explicitly - CLAUDE.md's open-source
analysis explicitly rejects a silent fallback (one reference project fell
back to Beijing for any unrecognized name; we do not).
"""

from __future__ import annotations

from app.core.errors import ValidationError

#: name -> nine-digit BOSS city code. Deliberately small and explicit -
#: adding a city means adding evidence here, never guessing one at runtime.
BOSS_CITY_IDS: dict[str, str] = {
    "北京": "101010100",
    "上海": "101020100",
    "广州": "101280100",
    "杭州": "101210100",
}


def city_id_for(city_name: str) -> str:
    """The structured BOSS city code for a known city name.

    Raises ``ValidationError`` (never guesses, never falls back to a default
    city) if the name is not in ``BOSS_CITY_IDS``.
    """
    try:
        return BOSS_CITY_IDS[city_name]
    except KeyError as exc:
        raise ValidationError(
            f"未知城市：{city_name}，无法生成搜索计划（不会使用默认城市代替）。",
            detail={"city": city_name, "known_cities": sorted(BOSS_CITY_IDS)},
        ) from exc
