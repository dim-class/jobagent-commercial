"""Application cycles reconstructed from event history (v0.6).

``Job.status`` tells you where a job is *now*. Analytics needs to know what
actually happened and when, and those differ: v0.4 lets the user undo a mistake
with ``status_reset``, so one job can carry several attempts.

A **cycle** starts at an ``applied`` event and ends at the next
``status_reset`` (or at the end of history). A cycle ended by a reset is
**superseded** - the user told us that application did not really happen, so it
must not sit in any denominator.

The effective cycle for a job is the most recent non-superseded one. Everything
downstream - maturity, latency, reply/interview counts - reads from it, never
from ``Job.status``.

Since v0.7 a cycle also carries **which resume the human actually submitted**,
read from the ``applied`` event itself. That attribution is fixed at the moment
of application and never re-derived: activating a different resume next week
must not retroactively change who gets credit for an interview. A cycle with no
recorded resume is ``ResumeUsage.unknown`` - a real answer, never a placeholder
for "probably the active one".

A human can fill in or correct an old attribution later. That appends a
corrective ``application_resume_attributed`` / ``application_resume_changed``
event naming its target cycle, so the original ``applied`` row is never edited
and the correction is itself auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.models import ApplicationEvent, EventType, Job, ResumeUsage
from app.models.enums import RESUME_ATTRIBUTION_EVENTS

#: Events that mark progress inside a cycle.
_REPLY_EVENTS = {EventType.replied}
_INTERVIEW_EVENTS = {EventType.interview}
_OFFER_EVENTS = {EventType.offer}
_REJECT_EVENTS = {EventType.rejected}


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


@dataclass(slots=True)
class ApplicationCycle:
    """One attempt at one job, as recorded by the human."""

    job_id: int
    applied_at: datetime
    superseded: bool = False
    replied_at: datetime | None = None
    interview_at: datetime | None = None
    offer_at: datetime | None = None
    rejected_at: datetime | None = None
    candidate_replied_at: datetime | None = None
    events: list[ApplicationEvent] = field(default_factory=list)

    # -- resume attribution (v0.7) ---------------------------------------

    #: The ``applied`` event that opened this cycle. Corrective attribution
    #: events point at this id, so a correction can never hit the wrong cycle.
    applied_event_id: int | None = None
    #: The resume actually submitted, as recorded at application time.
    resume_id: int | None = None
    #: Snapshot of the variant name for readability. The id stays authoritative.
    resume_variant_name: str | None = None
    resume_usage: ResumeUsage = ResumeUsage.unknown
    #: True when a human filled this in after the fact rather than at apply time.
    resume_attribution_corrected: bool = False

    # -- outcome flags ----------------------------------------------------

    @property
    def replied(self) -> bool:
        return self.replied_at is not None

    @property
    def interviewed(self) -> bool:
        return self.interview_at is not None

    @property
    def offered(self) -> bool:
        return self.offer_at is not None

    @property
    def rejected(self) -> bool:
        return self.rejected_at is not None

    @property
    def resume_attributed(self) -> bool:
        """Whether we know what the human submitted - either a specific resume
        or a confirmed "no resume". ``unknown`` is not attributed."""
        return self.resume_usage is not ResumeUsage.unknown

    # -- latency ----------------------------------------------------------

    def hours_to_reply(self) -> float | None:
        if self.replied_at is None:
            return None
        return max(0.0, (self.replied_at - self.applied_at).total_seconds() / 3600.0)

    def hours_to_interview(self) -> float | None:
        if self.interview_at is None:
            return None
        return max(0.0, (self.interview_at - self.applied_at).total_seconds() / 3600.0)

    # -- maturity ---------------------------------------------------------

    def age_days(self, *, now: datetime) -> float:
        return max(0.0, (_as_utc(now) - self.applied_at).total_seconds() / 86400.0)

    def is_response_mature(self, *, now: datetime, maturity_days: int) -> bool:
        """Old enough to count as a fair test of "did they reply?".

        A reply already received settles it regardless of age; otherwise the
        application must have had at least ``maturity_days`` to produce one.
        Without this, applying this morning would silently depress the rate.
        """
        if self.replied:
            return True
        return self.age_days(now=now) >= maturity_days

    def is_interview_mature(self, *, now: datetime, maturity_days: int) -> bool:
        if self.interviewed:
            return True
        return self.age_days(now=now) >= maturity_days

    def is_mature_no_response(self, *, now: datetime, maturity_days: int) -> bool:
        """Had a fair chance and heard nothing. Not the same as rejected."""
        return not self.replied and self.age_days(now=now) >= maturity_days


def _usage_of(event: ApplicationEvent) -> ResumeUsage:
    """Read the recorded usage, trusting the typed column over the metadata.

    A resume_id present always means ``used``, whatever the metadata claims -
    the FK is the authoritative link.
    """
    if event.resume_id is not None:
        return ResumeUsage.used
    raw = str((event.metadata_json or {}).get("resume_usage") or "").strip()
    try:
        usage = ResumeUsage(raw)
    except ValueError:
        return ResumeUsage.unknown
    # "used" without an id is not a usable attribution.
    return ResumeUsage.unknown if usage is ResumeUsage.used else usage


def _apply_attribution(cycle: ApplicationCycle, event: ApplicationEvent) -> None:
    cycle.resume_id = event.resume_id
    cycle.resume_usage = _usage_of(event)
    name = (event.metadata_json or {}).get("resume_variant_name")
    cycle.resume_variant_name = str(name) if name else None


def build_cycles(job: Job) -> list[ApplicationCycle]:
    """Every application attempt for one job, oldest first."""
    events = sorted(job.events, key=lambda e: (_as_utc(e.created_at), e.id))

    cycles: list[ApplicationCycle] = []
    current: ApplicationCycle | None = None
    #: applied-event id -> cycle, so a later correction finds its exact target
    #: even if the cycle has since been superseded.
    by_applied_event: dict[int, ApplicationCycle] = {}

    for event in events:
        moment = _as_utc(event.created_at)

        if event.event_type is EventType.applied:
            current = ApplicationCycle(
                job_id=job.id, applied_at=moment, applied_event_id=event.id
            )
            _apply_attribution(current, event)
            cycles.append(current)
            current.events.append(event)
            if event.id is not None:
                by_applied_event[event.id] = current
            continue

        if event.event_type in RESUME_ATTRIBUTION_EVENTS:
            # A human filling in or correcting an old attribution. It names its
            # target cycle explicitly and is applied even when that cycle was
            # superseded - correcting history must not depend on cycle order.
            target_id = (event.metadata_json or {}).get("applied_event_id")
            target = by_applied_event.get(target_id) if target_id is not None else None
            if target is None:
                # No explicit target: fall back to the open cycle, if any.
                target = current
            if target is not None:
                _apply_attribution(target, event)
                target.resume_attribution_corrected = True
                target.events.append(event)
            continue

        if event.event_type is EventType.status_reset:
            # The user withdrew this attempt; it must not count as an
            # application that failed to get a reply.
            if current is not None:
                current.superseded = True
                current.events.append(event)
            current = None
            continue

        if current is None:
            continue  # progress recorded outside any application attempt

        current.events.append(event)
        if event.event_type in _REPLY_EVENTS and current.replied_at is None:
            current.replied_at = moment
        elif event.event_type in _INTERVIEW_EVENTS and current.interview_at is None:
            current.interview_at = moment
        elif event.event_type in _OFFER_EVENTS and current.offer_at is None:
            current.offer_at = moment
        elif event.event_type in _REJECT_EVENTS and current.rejected_at is None:
            current.rejected_at = moment
        elif (
            event.event_type is EventType.candidate_reply
            and current.candidate_replied_at is None
        ):
            current.candidate_replied_at = moment

    return cycles


def effective_cycle(job: Job) -> ApplicationCycle | None:
    """The attempt analytics should count, or None if the job was never applied to.

    The most recent cycle that a ``status_reset`` did not withdraw. Earlier
    cycles are history, not current evidence, and are never mixed in.
    """
    for cycle in reversed(build_cycles(job)):
        if not cycle.superseded:
            return cycle
    return None


def in_window(cycle: ApplicationCycle, *, since: datetime | None) -> bool:
    """Window membership is decided by when the application happened."""
    return since is None or cycle.applied_at >= _as_utc(since)


def window_start(days: int | None, *, now: datetime) -> datetime | None:
    """``None`` for all-time - never an artificial cap."""
    if days is None:
        return None
    return _as_utc(now) - timedelta(days=days)
