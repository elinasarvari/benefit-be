from datetime import UTC, date, datetime

import pytest

from packages.platform.clock import CHICAGO, FixedClock, SystemClock, days_between, to_local, today


def test_FR_000_27_chicago_date_across_utc_midnight_and_dst() -> None:
    # Just after UTC midnight it is still the previous day in Chicago, in both summer and winter.
    summer = FixedClock(datetime(2026, 7, 1, 4, 30, tzinfo=UTC))
    winter = FixedClock(datetime(2026, 1, 10, 5, 30, tzinfo=UTC))

    assert today(summer) == date(2026, 6, 30)
    assert today(winter) == date(2026, 1, 9)


@pytest.mark.parametrize(
    ("instant", "expected_offset_hours"),
    [
        (datetime(2026, 3, 8, 7, 59, tzinfo=UTC), -6),  # just before DST starts (02:00 CST)
        (datetime(2026, 3, 8, 8, 0, tzinfo=UTC), -5),  # DST in effect
        (datetime(2026, 11, 1, 6, 59, tzinfo=UTC), -5),  # just before DST ends
        (datetime(2026, 11, 1, 7, 0, tzinfo=UTC), -6),  # standard time again
    ],
)
def test_FR_000_27_dst_boundaries_use_chicago_offsets(
    instant: datetime, expected_offset_hours: int
) -> None:
    offset = to_local(instant).utcoffset()

    assert offset is not None
    assert offset.total_seconds() == expected_offset_hours * 3600


def test_FR_000_27_days_between_counts_calendar_days() -> None:
    start = datetime(2026, 3, 7, 23, 0, tzinfo=CHICAGO)  # before DST starts
    end = datetime(2026, 3, 9, 1, 0, tzinfo=CHICAGO)

    assert days_between(start, end) == 2


def test_system_clock_returns_aware_utc() -> None:
    assert SystemClock().now().tzinfo is UTC
