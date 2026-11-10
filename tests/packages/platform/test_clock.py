from datetime import UTC, date, datetime, timedelta

import pytest

from packages.domain.case import CaseClock
from packages.platform.clock import (
    CHICAGO,
    FixedClock,
    SystemClock,
    case_now,
    case_today,
    days_between,
    to_local,
    today,
)


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


# --- Case clock (001, ADR-001-03) --------------------------------------------------------------

REAL_START = datetime(2026, 3, 17, 15, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("scale", "offset_seconds", "expected"),
    [
        (1, 0, REAL_START + timedelta(hours=2)),
        (1, 86400, REAL_START + timedelta(days=1, hours=2)),
        (1440, 0, REAL_START + timedelta(days=120)),  # 2 real hours = 120 case days
        (1440, 86400, REAL_START + timedelta(days=121)),
    ],
)
def test_FR_001_13_case_now_follows_scale_and_offset(
    scale: int, offset_seconds: int, expected: datetime
) -> None:
    clock = CaseClock(
        real_start=REAL_START, case_start=REAL_START, scale=scale, offset_seconds=offset_seconds
    )

    assert case_now(clock, REAL_START + timedelta(hours=2)) == expected
    assert case_now(clock, REAL_START) == REAL_START + timedelta(seconds=offset_seconds)


@pytest.mark.parametrize(
    ("real_now", "expected"),
    [
        (datetime(2026, 11, 1, 6, 30, tzinfo=UTC), date(2026, 11, 1)),  # 01:30 CDT, DST ends 02:00
        (datetime(2026, 3, 8, 7, 30, tzinfo=UTC), date(2026, 3, 8)),  # 01:30 CST, DST starts 02:00
        (datetime(2026, 12, 31, 5, 59, tzinfo=UTC), date(2026, 12, 30)),  # 23:59 CST previous day
    ],
)
def test_FR_001_18_case_today_is_chicago_date_across_dst_and_midnight_utc(
    real_now: datetime, expected: date
) -> None:
    clock = CaseClock(real_start=real_now, case_start=real_now, scale=1)

    assert case_today(clock, real_now) == expected
