from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Protocol
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from packages.domain.case import CaseClock

CHICAGO = ZoneInfo("America/Chicago")


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    def __init__(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            raise ValueError("FixedClock needs a timezone-aware datetime")
        self._instant = instant.astimezone(UTC)

    def now(self) -> datetime:
        return self._instant

    def advance_to(self, instant: datetime) -> None:
        self._instant = instant.astimezone(UTC)


def to_local(instant: datetime) -> datetime:
    """Convert an aware datetime to America/Chicago (FR-000-27)."""
    return instant.astimezone(CHICAGO)


def today(clock: Clock) -> date:
    return to_local(clock.now()).date()


def days_between(start: datetime, end: datetime) -> int:
    """Whole calendar days between two instants, counted in Chicago local dates."""
    return (to_local(end).date() - to_local(start).date()).days


def case_now(clock: CaseClock, real_now: datetime) -> datetime:
    """Case time for a real instant (ADR-001-03). Pure: the caller passes `real_now`.

    Workflow code passes `workflow.now()`, the API passes its `Clock.now()`, so both agree.
    """
    elapsed = real_now - clock.real_start
    return clock.case_start + elapsed * clock.scale + timedelta(seconds=clock.offset_seconds)


def case_today(clock: CaseClock, real_now: datetime) -> date:
    """The America/Chicago calendar date of the case clock (FR-001-18, FR-000-27)."""
    return to_local(case_now(clock, real_now)).date()
