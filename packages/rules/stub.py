from __future__ import annotations

from datetime import date
from decimal import Decimal

from packages.domain.eligibility import (
    Determination,
    EligibilityResult,
    RuleOutcome,
    RuleResult,
)
from packages.domain.household import HouseholdSnapshot
from packages.rules.rule_sets import POLICY_REFS


class StubRulesEngine:
    """Fixed Eligible result for consumers to code against until the real engine lands."""

    def evaluate(self, household: HouseholdSnapshot, as_of_date: date) -> Determination:
        return Determination(
            result=EligibilityResult.eligible,
            as_of_date=as_of_date,
            rule_set_id="IL-ADULT-STUB",
            rule_set_version=1,
            engine_version="stub",
            household_size=len(household.members),
            household_monthly_income=Decimal("0.00"),
            monthly_income_limit=Decimal("1782"),
            rule_results=[
                RuleResult(
                    rule_id=rule,
                    outcome=RuleOutcome.passed,
                    inputs={},
                    threshold=None,
                    policy_ref=ref,
                )
                for rule, ref in POLICY_REFS.items()
            ],
            missing_items=[],
        )
