"""Pure due-time functions for review reminders and escalation (FR-010-02, ADR-010-03).

Inputs and outputs are case-clock instants in UTC. Nothing here reads a clock (TW-1).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.platform.clock import CHICAGO

# ADR-010-06 (Accepted, option A): "elapsed" = 24-hour case-clock days. Counting Chicago calendar
# days instead would change only `due_at`.
REVIEW_DAY_COUNTING = "elapsed"


class ReviewWindows(Protocol):
    """`Settings` satisfies this; workflows get a `ReviewWindowSettings` copy in their input."""

    @property
    def REVIEW_REMINDER_DAYS(self) -> int: ...  # noqa: N802

    @property
    def REVIEW_ESCALATION_DAYS(self) -> int: ...  # noqa: N802


class ReviewWindowSettings(BaseModel):
    """The review wait settings, read by the starter and passed in (never read in a workflow)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    REVIEW_REMINDER_DAYS: int = Field(default=2, ge=1)
    REVIEW_ESCALATION_DAYS: int = Field(default=4, ge=1)

    @model_validator(mode="after")
    def _escalation_after_reminder(self) -> ReviewWindowSettings:
        if self.REVIEW_ESCALATION_DAYS <= self.REVIEW_REMINDER_DAYS:
            raise ValueError("REVIEW_ESCALATION_DAYS must be greater than REVIEW_REMINDER_DAYS")
        return self

    @classmethod
    def from_settings(cls, settings: ReviewWindows) -> ReviewWindowSettings:
        return cls(
            REVIEW_REMINDER_DAYS=settings.REVIEW_REMINDER_DAYS,
            REVIEW_ESCALATION_DAYS=settings.REVIEW_ESCALATION_DAYS,
        )


def due_at(opened_at_case: datetime, days: int) -> datetime:
    """The case-clock instant a task opened at `opened_at_case` is `days` old."""
    if REVIEW_DAY_COUNTING == "elapsed":
        return (opened_at_case + timedelta(days=days)).astimezone(UTC)
    raise ValueError(f"unknown review day counting {REVIEW_DAY_COUNTING!r}")


def coverage_end_passes(coverage_end: date) -> datetime:
    """Coverage end is a Chicago date (FR-000-27); it has passed at the next Chicago midnight."""
    next_day = datetime.combine(coverage_end + timedelta(days=1), time(0), tzinfo=CHICAGO)
    return next_day.astimezone(UTC)


def reminder_due(opened_at_case: datetime, settings: ReviewWindows) -> datetime:
    return due_at(opened_at_case, settings.REVIEW_REMINDER_DAYS)


def escalation_due(
    opened_at_case: datetime, coverage_end: date | None, settings: ReviewWindows
) -> datetime:
    """The earlier of the escalation window and the coverage end passing (redeterminations)."""
    due = due_at(opened_at_case, settings.REVIEW_ESCALATION_DAYS)
    if coverage_end is not None:
        due = min(due, coverage_end_passes(coverage_end))
    return due
