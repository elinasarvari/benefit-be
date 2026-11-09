from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

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
