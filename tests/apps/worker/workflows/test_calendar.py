from datetime import UTC, date, datetime

from apps.worker.workflows.calendar import chicago_date, days_between


def test_FR_000_27_chicago_date_across_utc_midnight() -> None:
    assert chicago_date(datetime(2026, 7, 1, 5, 30, tzinfo=UTC)) == date(2026, 7, 1)
    assert chicago_date(datetime(2026, 7, 1, 4, 30, tzinfo=UTC)) == date(2026, 6, 30)
    assert chicago_date(datetime(2026, 1, 10, 5, 30, tzinfo=UTC)) == date(2026, 1, 9)


def test_FR_000_27_days_between_counts_calendar_days_in_chicago() -> None:
    start = datetime(2026, 3, 8, 7, 0, tzinfo=UTC)  # 01:00 CST, the day DST starts
    end = datetime(2026, 3, 10, 6, 0, tzinfo=UTC)  # 01:00 CDT

    assert days_between(start, end) == 2
