from __future__ import annotations

from datetime import date, timedelta

import pytest

from packages.domain.eligibility import RuleId
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
