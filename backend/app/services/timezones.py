"""Local-day helpers for reporting.

Timestamps are stored in UTC. "Today" is a *local* concept, so every daily
metric converts to the configured reporting timezone (default ``Asia/Tokyo``)
before comparing dates. Comparing naive UTC dates would shift the boundary by
nine hours and silently mis-count everything applied late in the evening.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import get_settings
from app.core.logging import get_logger, log_event

logger = get_logger(__name__)

_FALLBACK = timezone.utc


def report_timezone() -> ZoneInfo | timezone:
    """The configured reporting timezone, falling back to UTC if unavailable."""
    name = get_settings().report_timezone
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):  # pragma: no cover - misconfig
        log_event(logger, "timezone.unavailable", requested=name)
        return _FALLBACK


def to_local(moment: datetime) -> datetime:
    """Interpret a stored timestamp as UTC and convert it to local time."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(report_timezone())


def local_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(report_timezone())


def local_today() -> date:
    return local_now().date()


def is_local_today(moment: datetime | None) -> bool:
    """True when ``moment`` falls on the current local calendar day."""
    if moment is None:
        return False
    return to_local(moment).date() == local_today()


def start_of_local_day(day: date | None = None) -> datetime:
    tz = report_timezone()
    target = day or local_now().date()
    return datetime.combine(target, time.min, tzinfo=tz)


def start_of_local_tomorrow() -> datetime:
    return start_of_local_day(local_now().date() + timedelta(days=1))
