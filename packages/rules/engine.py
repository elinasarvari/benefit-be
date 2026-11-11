"""The eligibility engine (KD-1, KD-2, FR-003-08).

`evaluate` is pure: no I/O, no clock, no randomness, no logging of inputs. The same snapshot and
as-of date always give the same `Determination`. `captured_at` is never read; the household size is
the number of members in the snapshot.

Result precedence (FR-003-20): any failed rule -> ineligible (still listing missing items);
otherwise any missing rule -> cannot_determine; otherwise eligible. A date with no rule set is
always cannot_determine with the missing item `rule_set`. Without a `self` member the person rules
are missing the item `applicant`.
"""

from __future__ import annotations

from datetime import date

from packages.domain.case import DeterminationOutcome
from packages.domain.eligibility import (
    Determination,
    MissingItem,
    RuleOutcome,
    RuleResult,
)
from packages.domain.household import HouseholdSnapshot, MemberFacts
from packages.rules.checks import (
    check_age,
    check_citizenship,
    check_identity,
    check_income,
    check_medicare,
    check_residency,
)
from packages.rules.income import household_income, monthly_limit
from packages.rules.rule_sets import rule_set_for

ENGINE_VERSION = "1.0.0"


def _applicant(members: list[MemberFacts]) -> MemberFacts | None:
    return next((m for m in members if m.relationship == "self"), None)


def _merge_missing(results: list[RuleResult]) -> list[MissingItem]:
    seen: set[tuple[str, object, object]] = set()
    merged: list[MissingItem] = []
    for result in results:
        for item in result.missing:
            key = (item.item, item.member_id, item.job_id)
            if key not in seen:
                seen.add(key)
                merged.append(item)
    return merged


def _result(results: list[RuleResult], has_rule_set: bool) -> DeterminationOutcome:
    outcomes = {r.outcome for r in results}
    if not has_rule_set:
        return DeterminationOutcome.cannot_determine
    if RuleOutcome.fail in outcomes:
        return DeterminationOutcome.ineligible
    if RuleOutcome.missing in outcomes:
        return DeterminationOutcome.cannot_determine
    return DeterminationOutcome.eligible


def evaluate(household: HouseholdSnapshot, as_of_date: date) -> Determination:
    rule_set = rule_set_for(as_of_date)
    members = household.members
    applicant = _applicant(members)
    results = [
        check_residency(applicant, rule_set),
        check_age(applicant, as_of_date, rule_set),
        check_citizenship(applicant, rule_set),
        check_identity(applicant, rule_set),
        check_medicare(applicant, rule_set),
        check_income(members, rule_set),
    ]
    size = len(members)
    return Determination(
        result=_result(results, rule_set is not None),
        as_of_date=as_of_date,
        rule_set_id=rule_set.rule_set_id if rule_set else None,
        rule_set_version=rule_set.version if rule_set else None,
        engine_version=ENGINE_VERSION,
        household_size=size,
        household_monthly_income=household_income(members).total,
        monthly_income_limit=monthly_limit(rule_set, size) if rule_set else None,
        rule_results=results,
        missing_items=_merge_missing(results),
    )
