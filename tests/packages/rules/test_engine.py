from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from packages.domain.eligibility import EligibilityResult, RuleId, RuleOutcome
from packages.domain.household import PayFrequency
from packages.rules import ENGINE_VERSION, evaluate
from tests.packages.rules.conftest import (
    AS_OF,
    as_of_dates,
    eligible_snapshot,
    job,
    member,
    snapshot,
    snapshots,
    stub,
    uid,
)

ORDER = list(RuleId)


def test_FR_003_01_household_meeting_every_rule_is_eligible() -> None:
    result = evaluate(eligible_snapshot(), AS_OF)

    assert result.result is EligibilityResult.eligible
    assert result.missing_items == []
    assert result.household_size == 1
    assert result.household_monthly_income == Decimal("1300.00")
    assert result.monthly_income_limit == Decimal("1782")
    assert result.engine_version == ENGINE_VERSION == "1.0.0"
    assert (result.rule_set_id, result.rule_set_version) == ("IL-ADULT-2026", 1)


@given(snapshots(), as_of_dates)
def test_FR_003_02_result_is_always_eligible_ineligible_or_cannot_determine(
    household: object, as_of: date
) -> None:
    result = evaluate(household, as_of)  # type: ignore[arg-type]

    assert result.result in set(EligibilityResult)


@pytest.mark.parametrize(
    ("fact", "item"),
    [
        ("citizenship_attested", "citizenship_attested"),
        ("medicare_entitled", "medicare_entitled"),
        ("identity_verified", "identity_verified"),
    ],
)
def test_FR_003_03_missing_fact_gives_cannot_determine_listing_item_not_ineligible(
    fact: str, item: str
) -> None:
    result = evaluate(snapshot([member(**{fact: None})]), AS_OF)  # type: ignore[arg-type]

    assert result.result is EligibilityResult.cannot_determine
    assert [m.item for m in result.missing_items] == [item]
    assert result.missing_items[0].member_id == uid("member-self")


def test_FR_003_03_job_without_stub_is_missing_pay_stub() -> None:
    result = evaluate(snapshot([member(jobs=[job("j", [])])]), AS_OF)

    assert result.result is EligibilityResult.cannot_determine
    assert [(m.item, m.job_id) for m in result.missing_items] == [("pay_stub", uid("j"))]
    assert result.household_monthly_income is None


_BLANKERS = ["citizenship_attested", "medicare_entitled", "identity_verified", "stubs"]


@given(st.sampled_from(_BLANKERS))
def test_FR_003_03_removing_information_from_eligible_household_never_gives_ineligible(
    blank: str,
) -> None:
    changes: dict[str, object] = {} if blank == "stubs" else {blank: None}
    if blank == "stubs":
        changes["jobs"] = [job("j", [])]

    result = evaluate(snapshot([member(**changes)]), AS_OF)  # type: ignore[arg-type]

    assert result.result is EligibilityResult.cannot_determine


@given(snapshots(), as_of_dates)
def test_FR_003_04_same_snapshot_and_date_give_identical_determination_json(
    household: object, as_of: date
) -> None:
    first = evaluate(household, as_of)  # type: ignore[arg-type]
    second = evaluate(household, as_of)  # type: ignore[arg-type]

    assert first.model_dump_json() == second.model_dump_json()


def test_FR_003_19_size_is_member_count_and_captured_at_does_not_change_result() -> None:
    people = [member(), member("child", relationship="child", declared_zero_income=True, jobs=[])]
    early = snapshot(people, captured_at=datetime(2025, 1, 1, tzinfo=UTC))
    late = snapshot(people, captured_at=datetime(2026, 12, 1, tzinfo=UTC))
    before = early.model_dump_json()

    first, second = evaluate(early, AS_OF), evaluate(late, AS_OF)

    assert first == second
    assert first.household_size == 2
    assert first.monthly_income_limit == Decimal("2380")
    assert early.model_dump_json() == before


def test_FR_003_20_failed_rule_plus_missing_info_is_ineligible_listing_missing_item() -> None:
    result = evaluate(snapshot([member(address_state="WI", citizenship_attested=None)]), AS_OF)

    assert result.result is EligibilityResult.ineligible
    assert [m.item for m in result.missing_items] == ["citizenship_attested"]
    outcomes = {r.rule_id: r.outcome for r in result.rule_results}
    assert outcomes[RuleId.ELIG_01] is RuleOutcome.fail
    assert outcomes[RuleId.ELIG_03] is RuleOutcome.missing


@given(snapshots(), as_of_dates)
def test_FR_003_20_result_follows_fail_then_missing_then_eligible_precedence(
    household: object, as_of: date
) -> None:
    result = evaluate(household, as_of)  # type: ignore[arg-type]
    outcomes = [r.outcome for r in result.rule_results]

    if result.rule_set_id is None:
        assert result.result is EligibilityResult.cannot_determine
    elif RuleOutcome.fail in outcomes:
        assert result.result is EligibilityResult.ineligible
    elif RuleOutcome.missing in outcomes:
        assert result.result is EligibilityResult.cannot_determine
    else:
        assert result.result is EligibilityResult.eligible


# --- US2: trace ---------------------------------------------------------------------------


def test_FR_003_05_income_ineligible_trace_shows_income_size_limit_year_and_policy_ref() -> None:
    household = snapshot([member(jobs=[job("j", [stub(PayFrequency.monthly, "2000")])])])

    result = evaluate(household, AS_OF)

    income = result.rule_results[5]
    assert result.result is EligibilityResult.ineligible
    assert income.rule_id is RuleId.ELIG_06
    assert income.outcome is RuleOutcome.fail
    assert income.threshold == "1782"
    assert income.policy_ref == "TM-3.1"
    assert income.inputs["household_monthly_income"] == "2000.00"
    assert income.inputs["household_size"] == "1"
    assert income.inputs["program_year"] == "2026"
    assert income.inputs[f"monthly_income.{uid('j')}"] == "2000.00"
    assert result.monthly_income_limit == Decimal("1782")


def test_FR_003_05_ineligible_result_names_every_failed_rule() -> None:
    household = snapshot(
        [member(address_state="WI", jobs=[job("j", [stub(PayFrequency.monthly, "2000")])])]
    )

    result = evaluate(household, AS_OF)

    failed = [r.rule_id for r in result.rule_results if r.outcome is RuleOutcome.fail]
    assert failed == [RuleId.ELIG_01, RuleId.ELIG_06]


def test_FR_003_05_trace_inputs_and_thresholds_per_rule() -> None:
    result = evaluate(eligible_snapshot(), AS_OF)

    by_rule = {r.rule_id: r for r in result.rule_results}
    assert by_rule[RuleId.ELIG_01].inputs == {"address_state": "IL"}
    assert by_rule[RuleId.ELIG_01].threshold == "IL"
    assert by_rule[RuleId.ELIG_02].inputs == {"age": "30", "as_of_date": "2026-06-01"}
    assert by_rule[RuleId.ELIG_02].threshold == "19-64"
    assert by_rule[RuleId.ELIG_03].inputs == {"citizenship_attested": "true"}
    assert by_rule[RuleId.ELIG_04].threshold == "true"
    assert by_rule[RuleId.ELIG_05].inputs == {"medicare_entitled": "false"}
    assert by_rule[RuleId.ELIG_05].threshold == "false"
    assert [r.policy_ref for r in result.rule_results] == [
        "TM-2.1",
        "TM-2.2",
        "TM-2.3",
        "TM-2.4",
        "TM-2.5",
        "TM-3.1",
    ]


@given(snapshots(), as_of_dates)
def test_FR_003_05_every_determination_has_six_complete_rule_results_in_id_order(
    household: object, as_of: date
) -> None:
    result = evaluate(household, as_of)  # type: ignore[arg-type]

    assert [r.rule_id for r in result.rule_results] == ORDER
    assert all(r.policy_ref for r in result.rule_results)
    keys = [(m.item, m.member_id, m.job_id) for m in result.missing_items]
    assert len(keys) == len(set(keys))
    for rule in result.rule_results:
        for item in rule.missing:
            assert item in result.missing_items


# --- US3: replay by as-of date -------------------------------------------------------------


def test_FR_003_07_as_of_date_in_2025_uses_2025_limit() -> None:
    household = eligible_snapshot()

    in_2025 = evaluate(household, date(2025, 6, 1))
    in_2026 = evaluate(household, date(2026, 6, 1))

    assert in_2025.monthly_income_limit == Decimal("1725")
    assert in_2026.monthly_income_limit == Decimal("1782")
    assert in_2025.rule_set_id == "IL-ADULT-2025"
    assert in_2025.rule_results[5].inputs["program_year"] == "2025"


@pytest.mark.parametrize("day", [date(2024, 12, 31), date(2027, 1, 1)])
def test_FR_003_07_date_without_rule_set_gives_cannot_determine_no_rules_for_date(
    day: date,
) -> None:
    result = evaluate(eligible_snapshot(), day)

    assert result.result is EligibilityResult.cannot_determine
    assert (result.rule_set_id, result.rule_set_version) == (None, None)
    assert [(m.item, m.reason) for m in result.missing_items] == [("rule_set", "no rules for date")]
    assert result.monthly_income_limit is None
    by_rule = {r.rule_id: r for r in result.rule_results}
    for rule in (RuleId.ELIG_01, RuleId.ELIG_02, RuleId.ELIG_06):
        assert by_rule[rule].outcome is RuleOutcome.missing
        assert by_rule[rule].threshold is None
    assert len(result.rule_results) == 6


def test_FR_003_16_no_self_member_is_cannot_determine_with_applicant_missing() -> None:
    result = evaluate(snapshot([member("s", relationship="spouse")]), AS_OF)

    assert result.result is EligibilityResult.cannot_determine
    assert [m.item for m in result.missing_items] == ["applicant"]
    assert len(result.rule_results) == 6
