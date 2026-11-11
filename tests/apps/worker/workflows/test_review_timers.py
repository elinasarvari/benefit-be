"""Due times for review reminders and escalation (FR-010-02, ADR-010-03, ADR-010-06 option A)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from apps.worker.workflows.review_timers import (
    REVIEW_DAY_COUNTING,
    ReviewWindowSettings,
    coverage_end_passes,
    escalation_due,
    reminder_due,
)

pytestmark = pytest.mark.unit

OPENED = datetime(2026, 3, 2, 23, 0, tzinfo=UTC)
WINDOWS = ReviewWindowSettings(REVIEW_REMINDER_DAYS=2, REVIEW_ESCALATION_DAYS=4)


@pytest.mark.parametrize(
    ("elapsed", "due"),
    [
        (timedelta(days=1, hours=23, minutes=59), False),
        (timedelta(days=2), True),
        (timedelta(days=2, minutes=1), True),
    ],
)
def test_FR_010_02_reminder_due_at_exactly_2_case_days(elapsed: timedelta, due: bool) -> None:
    case_now = OPENED + elapsed

    reminder_at = reminder_due(OPENED, WINDOWS)

    assert REVIEW_DAY_COUNTING == "elapsed"
    assert reminder_at.tzinfo is not None
    assert reminder_at.utcoffset() == timedelta(0)
    assert (case_now >= reminder_at) is due


@pytest.mark.parametrize(
    ("coverage_end", "expected"),
    [
        (None, OPENED + timedelta(days=4)),
        # Coverage ends on 3 March (Chicago); it has passed at the start of 4 March, Chicago time.
        (date(2026, 3, 3), datetime(2026, 3, 4, 6, 0, tzinfo=UTC)),
        (date(2026, 3, 20), OPENED + timedelta(days=4)),
    ],
    ids=["no-coverage-end", "coverage-end-before-4-days", "coverage-end-after-4-days"],
)
def test_FR_010_02_escalation_due_is_earlier_of_4_days_and_coverage_end(
    coverage_end: date | None, expected: datetime
) -> None:
    due = escalation_due(OPENED, coverage_end, WINDOWS)

    assert due == expected
    assert due.utcoffset() == timedelta(0)


def test_coverage_end_passes_at_start_of_next_chicago_day() -> None:
    assert coverage_end_passes(date(2026, 7, 1)) == datetime(2026, 7, 2, 5, 0, tzinfo=UTC)
