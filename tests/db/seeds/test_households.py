from __future__ import annotations

import re
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from db.seeds.households import SeededDataset, build_dataset, snapshot_for
from db.seeds.ids import HOUSEHOLD_A, HOUSEHOLD_B, SEED_ANCHOR_DATE, anchor_from_env
from packages.domain.eligibility import RuleId, RuleOutcome
from packages.rules import evaluate

ANCHOR = date(2026, 11, 10)
AS_OF = {2025: date(2025, 6, 1), 2026: date(2026, 6, 1)}
_dataset: dict[date, SeededDataset] = {}


def dataset(anchor: date = ANCHOR) -> SeededDataset:
    if anchor not in _dataset:
        _dataset[anchor] = build_dataset(anchor)
    return _dataset[anchor]


def _income_outcome(household: Any, year: int) -> RuleOutcome:
    determination = evaluate(snapshot_for(household, year), AS_OF[year])
    return next(r.outcome for r in determination.rule_results if r.rule_id is RuleId.ELIG_06)


def test_FR_003_10_dataset_has_200_households_two_years_sizes_1_to_6_and_60pct_under_limit() -> (
    None
):
    households = dataset().households

    assert len(households) == 200
    assert len({h.household_id for h in households}) == 200
    for year in (2025, 2026):
        assert all(year in h.years for h in households)
        sizes = {len(h.years[year].members) for h in households}
        assert sizes == {1, 2, 3, 4, 5, 6}
        under = sum(_income_outcome(h, year) is RuleOutcome.passed for h in households)
        assert 115 <= under <= 125, (year, under)


def test_FR_003_10_other_anchor_shifts_only_dates_and_default_anchor_is_2026_11_10() -> None:
    other = date(2026, 12, 1)

    def normalise(value: Any, anchor: date) -> Any:
        if isinstance(value, dict):
            return {k: normalise(v, anchor) for k, v in value.items()}
        if isinstance(value, list):
            return [normalise(v, anchor) for v in value]
        if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return (date.fromisoformat(value) - anchor).days
        return value

    def records(anchor: date) -> list[Any]:
        return [
            normalise(
                {
                    "id": str(h.household_id),
                    "years": {
                        year: {
                            "members": [m.model_dump(mode="json") for m in y.members],
                            "stubs": [s.model_dump(mode="json") for s in y.stubs],
                        }
                        for year, y in h.years.items()
                    },
                },
                anchor,
            )
            for h in dataset(anchor).households
        ]

    assert anchor_from_env({}) == date(2026, 11, 10)
    assert anchor_from_env({"SEED_ANCHOR_DATE": "2026-12-01"}) == other
    assert SEED_ANCHOR_DATE == anchor_from_env({})
    assert records(ANCHOR) == records(other)


def _mismatch_ratios(household: Any, year: int) -> list[Decimal]:
    ratios = []
    year_data = household.years[year]
    documented = {s.job_id: s for s in year_data.stubs}
    for member in year_data.members:
        for job in member.jobs:
            stub = documented.get(job.job_id)
            if stub is not None and stub.gross_pay > 0:
                ratios.append(abs(job.declared_gross - stub.gross_pay) / stub.gross_pay)
    return ratios


def test_FR_003_11_income_mismatches_within_15pct_and_special_households_present() -> None:
    households = dataset().households
    mismatched = [
        h for h in households if any(r > Decimal("0.05") for r in _mismatch_ratios(h, 2026))
    ]

    assert 24 <= len(mismatched) <= 36
    for h in mismatched:
        assert all(r <= Decimal("0.1501") for r in _mismatch_ratios(h, 2026))
    current = [h.years[2026] for h in households]
    assert any(sum(len(m.jobs) for m in y.members) >= 2 for y in current)
    assert any(y.members[0].declared_zero_income and not y.members[0].jobs for y in current)
    assert any(len(h.years[2025].members) != len(h.years[2026].members) for h in households)
    assert any(
        {job.job_id for m in y.members for job in m.jobs} - {s.job_id for s in y.stubs}
        for y in current
    )


def test_FR_003_14_demo_users_link_to_their_scenario_households() -> None:
    by_id = {h.household_id: h for h in dataset().households}
    household_a, household_b = by_id[HOUSEHOLD_A], by_id[HOUSEHOLD_B]
    as_of = date(2026, 6, 1)

    result_a = evaluate(snapshot_for(household_a, 2026), as_of)
    result_b = evaluate(snapshot_for(household_b, 2026), as_of)

    assert result_a.result.value == "eligible"
    assert [s.frequency.value for s in household_a.years[2026].stubs] == ["weekly"]
    assert result_b.household_monthly_income is not None
    assert result_b.household_monthly_income <= result_b.monthly_income_limit  # type: ignore[operator]
    assert any(r > Decimal("0.05") for r in _mismatch_ratios(household_b, 2026))
    assert "renewal-due" in household_b.scenarios


def test_FR_003_16_seeded_members_carry_attested_residency_citizenship_and_medicare() -> None:
    for household in dataset().households:
        for year, data in household.years.items():
            assert Counter(m.relationship for m in data.members)["self"] == 1
            for member in data.members:
                assert member.coverage_year == year
                assert member.household_id == household.household_id
                assert re.fullmatch(r"[A-Z]{2}", member.address_state)
                assert member.citizenship_attested is not None
                assert member.medicare_entitled is not None


def test_FR_003_10_members_are_adults_19_to_64_for_applicants_in_both_years() -> None:
    for household in dataset().households:
        for year, data in household.years.items():
            applicant = data.members[0]
            age = (AS_OF[year] - applicant.date_of_birth) // timedelta(days=365)
            assert 19 <= age <= 64
