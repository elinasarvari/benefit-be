"""Exact decimal income counting from pay-stub facts (ADR-003-02, FR-003-15/17/18)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from packages.domain.eligibility import MissingItem, RuleSet
from packages.domain.household import MemberFacts, PayFrequency, PayStubFact

CENT = Decimal("0.01")
_PERIODS_PER_YEAR = {
    PayFrequency.weekly: Decimal(52),
    PayFrequency.biweekly: Decimal(26),
    PayFrequency.semimonthly: Decimal(24),
    PayFrequency.monthly: Decimal(12),
}
_MONTHS = Decimal(12)


@dataclass(frozen=True)
class HouseholdIncome:
    """`total` is None when any income is missing; `per_job` holds the jobs that have a stub."""

    total: Decimal | None
    per_job: dict[UUID, Decimal] = field(default_factory=dict)
    missing: list[MissingItem] = field(default_factory=list)


def monthly_income_from_stub(stub: PayStubFact) -> Decimal:
    exact = stub.gross_pay * _PERIODS_PER_YEAR[stub.frequency] / _MONTHS
    return exact.quantize(CENT, rounding=ROUND_HALF_UP)


def monthly_limit(rule_set: RuleSet, size: int) -> Decimal:
    guideline = rule_set.poverty_guideline
    annual = guideline.base_annual + guideline.per_additional_annual * (max(size, 1) - 1)
    return Decimal(int(annual * rule_set.fpl_percent / 100 // _MONTHS))


def household_income(members: Sequence[MemberFacts]) -> HouseholdIncome:
    total = Decimal("0.00")
    per_job: dict[UUID, Decimal] = {}
    missing: list[MissingItem] = []
    for person in members:
        if not person.jobs and not person.declared_zero_income:
            missing.append(
                MissingItem(
                    item="income",
                    member_id=person.member_id,
                    reason="no job and no zero-income declaration",
                )
            )
        for income in person.jobs:
            if not income.stubs:
                missing.append(
                    MissingItem(
                        item="pay_stub",
                        member_id=person.member_id,
                        job_id=income.job_id,
                        reason="no usable pay stub for this job",
                    )
                )
                continue
            latest = max(income.stubs, key=lambda s: (s.pay_date, str(s.document_id)))
            monthly = monthly_income_from_stub(latest)
            per_job[income.job_id] = monthly
            total += monthly
    return HouseholdIncome(
        total=None if missing else total.quantize(CENT), per_job=per_job, missing=missing
    )
