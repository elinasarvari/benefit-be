from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import given

from packages.domain.eligibility import RuleId, RuleOutcome, RuleSet
from packages.domain.household import MemberFacts
from packages.rules.checks import (
    age_on,
    check_age,
    check_citizenship,
    check_identity,
    check_medicare,
    check_residency,
)
from packages.rules.engine import evaluate
from packages.rules.rule_sets import rule_set_for
from tests.packages.rules.conftest import (
    AS_OF,
    dates_1940_2026,
    member,
    snapshot,
    years_before,
)


def _rule_set() -> RuleSet:
    found = rule_set_for(AS_OF)
    assert found is not None
    return found


@pytest.mark.parametrize(
    ("rule", "overrides"),
    [
        (RuleId.ELIG_01, {"address_state": "WI"}),
        (RuleId.ELIG_02, {"date_of_birth": date(1956, 1, 1)}),
        (RuleId.ELIG_03, {"citizenship_attested": False}),
        (RuleId.ELIG_04, {"identity_verified": False}),
        (RuleId.ELIG_05, {"medicare_entitled": True}),
    ],
)
def test_FR_003_01_each_person_rule_fails_on_its_violation(
    rule: RuleId, overrides: dict[str, object]
) -> None:
    result = evaluate(snapshot([member(**overrides)]), AS_OF)  # type: ignore[arg-type]

    by_rule = {r.rule_id: r.outcome for r in result.rule_results}
    assert by_rule[rule] is RuleOutcome.fail
    assert [r for r, o in by_rule.items() if o is RuleOutcome.fail] == [rule]


def _age_outcome(applicant: MemberFacts) -> RuleOutcome:
    return check_age(applicant, AS_OF, _rule_set()).outcome


def test_FR_003_01_age_evaluated_on_as_of_date_at_19_and_65_boundaries() -> None:
    turn_19 = years_before(AS_OF, 19)
    turn_65 = years_before(AS_OF, 65)

    assert _age_outcome(member(date_of_birth=turn_19)) is RuleOutcome.passed
    assert _age_outcome(member(date_of_birth=turn_19 + timedelta(days=1))) is RuleOutcome.fail
    assert _age_outcome(member(date_of_birth=turn_65)) is RuleOutcome.fail
    assert _age_outcome(member(date_of_birth=turn_65 + timedelta(days=1))) is RuleOutcome.passed
    leap = member(date_of_birth=date(2004, 2, 29))
    assert age_on(leap.date_of_birth, date(2023, 2, 28)) == 18
    assert age_on(leap.date_of_birth, date(2023, 3, 1)) == 19


@given(dob=dates_1940_2026)
def test_FR_003_01_age_rule_passes_iff_whole_years_in_19_to_64(dob: date) -> None:
    if dob > AS_OF:
        return
    # independent check: the 19th birthday is on or before AS_OF and the 65th is after it
    birthday_19 = _birthday(dob, 19)
    birthday_65 = _birthday(dob, 65)

    assert (_age_outcome(member(date_of_birth=dob)) is RuleOutcome.passed) == (
        birthday_19 <= AS_OF < birthday_65
    )


def _birthday(dob: date, years: int) -> date:
    if (dob.month, dob.day) == (2, 29):
        try:
            return dob.replace(year=dob.year + years)
        except ValueError:
            return date(dob.year + years, 3, 1)
    return dob.replace(year=dob.year + years)


def test_FR_003_16_person_rules_use_applicant_only_and_no_self_is_missing_applicant() -> None:
    spouse = member(
        "spouse",
        relationship="spouse",
        medicare_entitled=True,
        address_state="WI",
        declared_zero_income=True,
        jobs=[],
    )
    result = evaluate(snapshot([member(), spouse]), AS_OF)
    no_self = evaluate(snapshot([spouse]), AS_OF)

    assert result.result.value == "eligible"
    assert no_self.result.value == "cannot_determine"
    assert [m.item for m in no_self.missing_items] == ["applicant"]


def test_FR_003_16_missing_fact_results_carry_member_id_and_item() -> None:
    applicant = member(citizenship_attested=None, identity_verified=None, medicare_entitled=None)

    results = [
        check_citizenship(applicant, _rule_set()),
        check_identity(applicant, _rule_set()),
        check_medicare(applicant, _rule_set()),
        check_residency(applicant, _rule_set()),
    ]

    assert [r.outcome for r in results] == [RuleOutcome.missing] * 3 + [RuleOutcome.passed]
    assert [r.missing[0].item for r in results[:3]] == [
        "citizenship_attested",
        "identity_verified",
        "medicare_entitled",
    ]
    assert all(r.missing[0].member_id == applicant.member_id for r in results[:3])
