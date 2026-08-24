"""Job source adapters.

Implemented:
    manual  - the user pastes a JD                       (v0.1)
    boss     - BOSS 直聘, read from a visible browser     (v0.2)

Planned (folders stay empty until a real, terms-respecting,
human-in-the-loop adapter is written):
    liepin/  - 猎聘             (v0.3)
    zhaopin/ - 智联招聘         (v0.3)
    job51/   - 前程无忧 51job   (v0.3)
"""

from app.job_sources.base import (
    BrowserJobSource,
    JobSearchQuery,
    JobSource,
    RawJobPosting,
)
from app.job_sources.boss import BossJobSource, boss_source
from app.job_sources.manual import ManualJobSource, manual_source

#: Registry consulted by the API layer. Future adapters register themselves here.
SOURCES: dict[str, JobSource] = {
    manual_source.name: manual_source,
    boss_source.name: boss_source,
}

#: Sources that read a live browser page, keyed by name.
BROWSER_SOURCES: dict[str, BrowserJobSource] = {boss_source.name: boss_source}


def get_source(name: str) -> JobSource:
    try:
        return SOURCES[name]
    except KeyError as exc:  # pragma: no cover - guarded by request validation
        raise KeyError(f"unknown job source: {name!r}") from exc


def get_browser_source(name: str) -> BrowserJobSource:
    try:
        return BROWSER_SOURCES[name]
    except KeyError as exc:  # pragma: no cover - guarded by site detection
        raise KeyError(f"unknown browser job source: {name!r}") from exc


__all__ = [
    "BROWSER_SOURCES",
    "BossJobSource",
    "BrowserJobSource",
    "JobSearchQuery",
    "JobSource",
    "ManualJobSource",
    "RawJobPosting",
    "SOURCES",
    "boss_source",
    "get_browser_source",
    "get_source",
    "manual_source",
]
