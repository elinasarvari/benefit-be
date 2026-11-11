from __future__ import annotations

from datetime import date, timedelta

import pytest

from packages.domain.eligibility import RuleId, RuleSet
from packages.rules.rule_sets import RULE_SETS, rule_set_for


def test_FR_003_06_rule_sets_cover_2025_and_2026_without_gap_or_overlap() -> None:
    ordered = sorted(RULE_SETS, key=lambda rs: rs.effective_from)

    assert [rs.rule_set_id for rs in ordered] == ["IL-ADULT-2025", "IL-ADULT-2026"]
    assert all(rs.version == 1 for rs in ordered)
    assert ordered[0].effective_from == date(2025, 1, 1)
    assert ordered[0].effective_to + timedelta(days=1) == ordered[1].effective_from
    assert ordered[1].effective_to == date(2026, 12, 31)
    assert [rs.program_year for rs in ordered] == [2025, 2026]
    assert all(set(rs.policy_refs) == set(RuleId) for rs in ordered)
    assert ordered[1].policy_refs[RuleId.ELIG_06] == "TM-3.1"


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2025, 1, 1), "IL-ADULT-2025"),
        (date(2025, 12, 31), "IL-ADULT-2025"),
        (date(2026, 1, 1), "IL-ADULT-2026"),
        (date(2026, 12, 31), "IL-ADULT-2026"),
    ],
)
def test_FR_003_07_year_boundary_dates_select_their_calendar_year_rule_set(
    day: date, expected: str
) -> None:
    found = rule_set_for(day)

    assert found is not None
    assert found.rule_set_id == expected


@pytest.mark.parametrize("day", [date(2024, 12, 31), date(2027, 1, 1)])
def test_FR_003_07_dates_outside_every_rule_set_have_none(day: date) -> None:
    assert rule_set_for(day) is None


def test_FR_003_06_seeded_rule_set_documents_equal_RULE_SETS() -> None:
    from db.seeds.rule_sets import rule_set_documents

    documents = rule_set_documents()

    assert [d["_id"] for d in documents] == [f"{rs.rule_set_id}@{rs.version}" for rs in RULE_SETS]
    for document, rule_set in zip(documents, RULE_SETS, strict=True):
        stored = {k: v for k, v in document.items() if k != "_id"}
        assert RuleSet.model_validate(stored) == rule_set


def test_FR_003_07_same_household_as_of_2025_and_2026_uses_each_years_limit() -> None:
    from decimal import Decimal

    from packages.domain.case import DeterminationOutcome
    from packages.rules.engine import evaluate
    from tests.packages.rules.conftest import job, member, snapshot, stub

    earner = member(
        "self",
        jobs=[job("j", [stub("monthly", Decimal("2350"), date(2025, 6, 20))])],
    )
    spouse = member("spouse", relationship="spouse", jobs=[], declared_zero_income=True)
    household = snapshot([earner, spouse])

    in_2025 = evaluate(household, date(2025, 7, 1))
    in_2026 = evaluate(household, date(2026, 7, 1))

    assert in_2025.result is DeterminationOutcome.ineligible
    assert in_2025.monthly_income_limit == Decimal("2300")
    assert in_2025.rule_set_id == "IL-ADULT-2025"
    assert in_2026.result is DeterminationOutcome.eligible
    assert in_2026.monthly_income_limit == Decimal("2380")
    assert in_2026.rule_set_id == "IL-ADULT-2026"
