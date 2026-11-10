"""Chicago calendar helpers for workflow code (FR-000-27, TW-1).

They take instants (always `workflow.now()`), never read the clock, and load the time zone through
the sandbox pass-through so no zone files are read inside workflow code.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from packages.domain.case import CaseClock
    from packages.platform.clock import CHICAGO, case_now, case_today, to_local
    from packages.platform.clock import days_between as _days_between


def chicago_date(instant: datetime) -> date:
    return to_local(instant).date()


def days_between(start: datetime, end: datetime) -> int:
    return _days_between(start, end)


def case_now_wf(clock: CaseClock) -> datetime:
    """Case time now, from `workflow.now()` (ADR-001-03). Only call this inside workflow code."""
    return case_now(clock, workflow.now())


def case_today_wf(clock: CaseClock) -> date:
    """Chicago calendar date of the case clock now (FR-001-18). Only call inside workflow code."""
    return case_today(clock, workflow.now())


MIN_WAKE = timedelta(seconds=1)


def until_next_case_day(clock: CaseClock) -> timedelta:
    """Real time until the case clock reaches the next Chicago midnight.

    A query sees `workflow.now()` as of the last workflow task, so a long wait wakes at each case
    day to keep `status` (days remaining) exact (R3). Waking takes no action.
    """
    now = case_now_wf(clock)
    tomorrow = to_local(now).date() + timedelta(days=1)
    midnight = datetime.combine(tomorrow, time(0), tzinfo=CHICAGO)
    return max((midnight - now) / clock.scale, MIN_WAKE)
