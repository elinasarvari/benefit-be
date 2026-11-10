from __future__ import annotations

from datetime import date
from decimal import Decimal

from packages.domain.eligibility import (
    Determination,
    EligibilityResult,
    RuleId,
    RuleOutcome,
    RuleResult,
)
from packages.domain.household import HouseholdSnapshot

_POLICY_REFS = {
    RuleId.ELIG_01: "TM-2.1",
    RuleId.ELIG_02: "TM-2.2",
    RuleId.ELIG_03: "TM-2.3",
    RuleId.ELIG_04: "TM-2.4",
    RuleId.ELIG_05: "TM-2.5",
    RuleId.ELIG_06: "TM-3.1",
}


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
                for rule, ref in _POLICY_REFS.items()
            ],
            missing_items=[],
        )
