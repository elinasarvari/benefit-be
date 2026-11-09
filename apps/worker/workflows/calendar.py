"""Chicago calendar helpers for workflow code (FR-000-27, TW-1).

They take instants (always `workflow.now()`), never read the clock, and load the time zone through
the sandbox pass-through so no zone files are read inside workflow code.
"""

from __future__ import annotations

from datetime import date, datetime

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from packages.platform.clock import days_between as _days_between
    from packages.platform.clock import to_local


def chicago_date(instant: datetime) -> date:
    return to_local(instant).date()


def days_between(start: datetime, end: datetime) -> int:
    return _days_between(start, end)
