"""One pure function per rule, ELIG-01 to ELIG-06 (FR-003-01, FR-003-05, FR-003-16).

Person rules look at the applicant only (the `self` member). A `None` fact is `missing`, never
`fail`. Trace values are canonical strings and use member IDs only, never names (VIII).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from packages.domain.eligibility import MissingItem, RuleId, RuleOutcome, RuleResult, RuleSet
from packages.domain.household import MemberFacts
from packages.rules.income import household_income, monthly_limit
from packages.rules.rule_sets import POLICY_REFS

NO_RULE_SET = "no rules for date"


def age_on(date_of_birth: date, as_of_date: date) -> int:
    """Whole years on `as_of_date`; a 29 February birthday is reached on 1 March."""
    birthday = (date_of_birth.month, date_of_birth.day)
    if birthday == (2, 29) and not _is_leap(as_of_date.year):
        birthday = (3, 1)
    return as_of_date.year - date_of_birth.year - ((as_of_date.month, as_of_date.day) < birthday)


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _ref(rule: RuleId, rule_set: RuleSet | None) -> str:
    return rule_set.policy_refs[rule] if rule_set else POLICY_REFS[rule]


def _applicant_missing(rule: RuleId, rule_set: RuleSet | None) -> RuleResult:
    return RuleResult(
        rule_id=rule,
        outcome=RuleOutcome.missing,
        inputs={},
        threshold=None,
        policy_ref=_ref(rule, rule_set),
        missing=[MissingItem(item="applicant", reason="household has no applicant (self) member")],
    )


def _no_rule_set(rule: RuleId, inputs: dict[str, str]) -> RuleResult:
    return RuleResult(
        rule_id=rule,
        outcome=RuleOutcome.missing,
        inputs=inputs,
        threshold=None,
        policy_ref=POLICY_REFS[rule],
        missing=[MissingItem(item="rule_set", reason=NO_RULE_SET)],
    )


def _fact_rule(
    rule: RuleId,
    applicant: MemberFacts | None,
    rule_set: RuleSet | None,
    name: str,
    expected: bool,
) -> RuleResult:
    if applicant is None:
        return _applicant_missing(rule, rule_set)
    value: bool | None = getattr(applicant, name)
    inputs = {name: "unknown" if value is None else str(value).lower()}
    if value is None:
        outcome = RuleOutcome.missing
        missing = [
            MissingItem(
                item=name, member_id=applicant.member_id, reason=f"{name} has not been answered"
            )
        ]
    else:
        outcome = RuleOutcome.passed if value is expected else RuleOutcome.fail
        missing = []
    return RuleResult(
        rule_id=rule,
        outcome=outcome,
        inputs=inputs,
        threshold=str(expected).lower(),
        policy_ref=_ref(rule, rule_set),
        missing=missing,
    )


def check_residency(applicant: MemberFacts | None, rule_set: RuleSet | None) -> RuleResult:
    rule = RuleId.ELIG_01
    if applicant is None:
        return _applicant_missing(rule, rule_set)
    inputs = {"address_state": applicant.address_state}
    if rule_set is None:
        return _no_rule_set(rule, inputs)
    outcome = RuleOutcome.passed if applicant.address_state == rule_set.state else RuleOutcome.fail
    return RuleResult(
        rule_id=rule,
        outcome=outcome,
        inputs=inputs,
        threshold=rule_set.state,
        policy_ref=_ref(rule, rule_set),
    )


def check_age(
    applicant: MemberFacts | None, as_of_date: date, rule_set: RuleSet | None
) -> RuleResult:
    rule = RuleId.ELIG_02
    if applicant is None:
        return _applicant_missing(rule, rule_set)
    age = age_on(applicant.date_of_birth, as_of_date)
    inputs = {"age": str(age), "as_of_date": as_of_date.isoformat()}
    if rule_set is None:
        return _no_rule_set(rule, inputs)
    within = rule_set.age_min <= age <= rule_set.age_max
    return RuleResult(
        rule_id=rule,
        outcome=RuleOutcome.passed if within else RuleOutcome.fail,
        inputs=inputs,
        threshold=f"{rule_set.age_min}-{rule_set.age_max}",
        policy_ref=_ref(rule, rule_set),
    )


def check_citizenship(applicant: MemberFacts | None, rule_set: RuleSet | None) -> RuleResult:
    return _fact_rule(RuleId.ELIG_03, applicant, rule_set, "citizenship_attested", True)


def check_identity(applicant: MemberFacts | None, rule_set: RuleSet | None) -> RuleResult:
    return _fact_rule(RuleId.ELIG_04, applicant, rule_set, "identity_verified", True)


def check_medicare(applicant: MemberFacts | None, rule_set: RuleSet | None) -> RuleResult:
    return _fact_rule(RuleId.ELIG_05, applicant, rule_set, "medicare_entitled", False)


def check_income(members: Sequence[MemberFacts], rule_set: RuleSet | None) -> RuleResult:
    """ELIG-06: household monthly income at or below the monthly limit (inclusive)."""
    rule = RuleId.ELIG_06
    size = len(members)
    income = household_income(members)
    inputs = {"household_size": str(size)}
    inputs.update({f"monthly_income.{job}": str(value) for job, value in income.per_job.items()})
    if income.total is not None:
        inputs["household_monthly_income"] = str(income.total)
    if rule_set is None:
        result = _no_rule_set(rule, inputs)
        return result.model_copy(update={"missing": [*result.missing, *income.missing]})
    inputs["program_year"] = str(rule_set.program_year)
    limit = monthly_limit(rule_set, size)
    if income.total is None:
        outcome = RuleOutcome.missing
    else:
        outcome = RuleOutcome.passed if income.total <= limit else RuleOutcome.fail
    return RuleResult(
        rule_id=rule,
        outcome=outcome,
        inputs=inputs,
        threshold=str(limit),
        policy_ref=_ref(rule, rule_set),
        missing=income.missing,
    )
