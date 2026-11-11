"""Property tests for the engine (KD-3, FR-003-04, -08, -20, -01)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from packages.domain.case import DeterminationOutcome
from packages.domain.eligibility import RuleId, RuleOutcome
from packages.domain.household import MemberFacts
from packages.rules.engine import evaluate
from packages.rules.income import monthly_limit
from packages.rules.rule_sets import RULE_SETS, rule_set_for
from tests.packages.rules.conftest import (
    as_of_dates,
    eligible_snapshot,
    member,
    snapshot,
    snapshots,
)

PERSON_RULES = [RuleId.ELIG_01, RuleId.ELIG_02, RuleId.ELIG_03, RuleId.ELIG_04, RuleId.ELIG_05]


@settings(max_examples=100, derandomize=True)
@given(snapshots(), as_of_dates)
def test_FR_003_04_property_same_inputs_give_equal_determination(snap, as_of) -> None:
    assert evaluate(snap, as_of) == evaluate(snap, as_of)
    assert evaluate(snap, as_of).model_dump_json() == evaluate(snap, as_of).model_dump_json()


@settings(max_examples=100, derandomize=True)
@given(
    st.sampled_from(RULE_SETS),
    st.integers(1, 6),
    st.sampled_from(["at", "below", "above"]),
)
def test_FR_003_01_property_income_at_or_below_limit_passes_income_rule(
    rule_set, size, where
) -> None:
    limit = monthly_limit(rule_set, size)
    gross = {"at": limit, "below": limit - Decimal("0.01"), "above": limit + Decimal("0.01")}[where]
    # One monthly stub on the applicant so income equals `gross` exactly.
    from tests.packages.rules.conftest import job, stub

    earner = member("self", jobs=[job("j", [stub("monthly", gross, rule_set.effective_from)])])
    others = [
        member(f"m{i}", relationship="child", jobs=[], declared_zero_income=True)
        for i in range(size - 1)
    ]
    result = evaluate(snapshot([earner, *others]), rule_set.effective_from)
    income = next(r for r in result.rule_results if r.rule_id is RuleId.ELIG_06)

    expected = RuleOutcome.fail if where == "above" else RuleOutcome.passed
    assert income.outcome is expected


@settings(max_examples=100, derandomize=True)
@given(snapshots(), as_of_dates)
def test_FR_003_20_property_any_fail_gives_ineligible_and_missing_only_gives_cannot_determine(
    snap, as_of
) -> None:
    result = evaluate(snap, as_of)
    outcomes = {r.outcome for r in result.rule_results}

    if rule_set_for(as_of) is None:
        assert result.result is DeterminationOutcome.cannot_determine
    elif RuleOutcome.fail in outcomes:
        assert result.result is DeterminationOutcome.ineligible
    elif RuleOutcome.missing in outcomes:
        assert result.result is DeterminationOutcome.cannot_determine
        assert result.missing_items
    else:
        assert result.result is DeterminationOutcome.eligible


@settings(max_examples=100, derandomize=True)
@given(snapshots(), as_of_dates)
def test_FR_003_08_property_evaluate_leaves_snapshot_unchanged(snap, as_of) -> None:
    before = snap.model_dump_json()

    evaluate(snap, as_of)

    assert snap.model_dump_json() == before


def test_FR_003_20_property_helper_sanity_eligible_snapshot_is_eligible() -> None:
    """Guards the helpers the properties rely on: the base snapshot is Eligible."""
    assert evaluate(eligible_snapshot(), date(2026, 6, 1)).result is DeterminationOutcome.eligible
    assert isinstance(member(), MemberFacts)
