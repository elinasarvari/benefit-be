from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given

from packages.domain.eligibility import MissingItem
from packages.domain.household import PayFrequency
from packages.rules.income import household_income, monthly_income_from_stub, monthly_limit
from packages.rules.rule_sets import rule_set_for
from tests.packages.rules.conftest import frequencies as frequency_strategy
from tests.packages.rules.conftest import job, member, money, stub, uid


@pytest.mark.parametrize(
    ("year", "size", "limit"),
    [
        (2026, 1, 1782),
        (2026, 2, 2380),
        (2026, 3, 2978),
        (2026, 4, 3576),
        (2026, 5, 4174),
        (2026, 6, 4772),
        (2025, 1, 1725),
        (2025, 2, 2300),
        (2025, 3, 2875),
        (2025, 4, 3450),
        (2025, 5, 4025),
        (2025, 6, 4600),
    ],
)
def test_FR_003_15_monthly_limit_is_floor_of_138_percent_annual_over_12(
    year: int, size: int, limit: int
) -> None:
    rule_set = rule_set_for(date(year, 6, 1))
    assert rule_set is not None

    assert monthly_limit(rule_set, size) == Decimal(limit)


def test_FR_003_17_household_income_sums_all_members_and_all_jobs() -> None:
    first = member(
        "self",
        jobs=[
            job("a", [stub("weekly", "400", name="a")]),
            job("b", [stub("monthly", "1500", name="b")]),
        ],
    )
    second = member(
        "spouse",
        relationship="spouse",
        jobs=[job("c", [stub("biweekly", "1000", name="c")])],
    )

    income = household_income([first, second])

    assert income.total == Decimal("1733.33") + Decimal("1500.00") + Decimal("2166.67")
    assert income.missing == []
    assert income.per_job == {
        uid("a"): Decimal("1733.33"),
        uid("b"): Decimal("1500.00"),
        uid("c"): Decimal("2166.67"),
    }


def test_FR_003_17_zero_declared_counts_zero_and_job_without_stub_is_missing() -> None:
    zero = household_income([member("self", declared_zero_income=True, jobs=[])])
    no_stub = household_income([member("self", jobs=[job("j", [])])])

    assert zero.total == Decimal("0.00")
    assert zero.missing == []
    assert no_stub.total is None
    assert no_stub.missing == [
        MissingItem(
            item="pay_stub",
            member_id=uid("member-self"),
            job_id=uid("j"),
            reason="no usable pay stub for this job",
        )
    ]


@pytest.mark.parametrize(
    ("frequency", "gross", "expected"),
    [
        ("weekly", "400", "1733.33"),
        ("biweekly", "1000", "2166.67"),
        ("semimonthly", "900", "1800.00"),
        ("monthly", "1500", "1500.00"),
        ("weekly", "0.01", "0.04"),
        ("weekly", "10.38", "44.98"),  # 44.98 exactly: 10.38 x 52 / 12 = 44.98
        ("weekly", "0.105", "0.46"),  # 0.455 rounds half up
    ],
)
def test_FR_003_18_stub_converts_to_monthly_by_frequency_using_latest_stub(
    frequency: str, gross: str, expected: str
) -> None:
    assert monthly_income_from_stub(stub(frequency, gross)) == Decimal(expected)


def test_FR_003_18_latest_pay_date_wins_within_a_job() -> None:
    older = stub("weekly", "100", pay_date=date(2026, 5, 1), name="old")
    newer = stub("weekly", "400", pay_date=date(2026, 5, 22), name="new")

    income = household_income([member("self", jobs=[job("j", [newer, older])])])

    assert income.total == Decimal("1733.33")


@given(frequency=frequency_strategy, gross=money)
def test_FR_003_18_monthly_income_is_non_negative_decimal_with_two_places(
    frequency: PayFrequency, gross: Decimal
) -> None:
    result = monthly_income_from_stub(stub(frequency, gross))

    assert result >= 0
    assert result == result.quantize(Decimal("0.01"))
    assert result.as_tuple().exponent == -2
