"""Contract stub of `explain_determination` (FR-004-19, FR-011-07): the rule trace, until 004a."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from packages.domain.case import DeterminationOutcome
from packages.domain.eligibility import (
    Determination,
    DeterminationRecord,
    RuleId,
    RuleOutcome,
    RuleResult,
)
from packages.domain.explanation import ExplanationAudience
from packages.rules.explanations import NO_CITATION, explain_determination


def _record() -> DeterminationRecord:
    rules = [
        RuleResult(
            rule_id=rule_id,
            outcome=RuleOutcome.fail if rule_id is RuleId.ELIG_06 else RuleOutcome.passed,
            inputs={},
            threshold=None,
            policy_ref="",
            missing=[],
        )
        for rule_id in RuleId
    ]
    return DeterminationRecord(
        determination_id=uuid4(),
        household_id=uuid4(),
        case_id=uuid4(),
        snapshot_id=uuid4(),
        recorded_at=datetime(2026, 3, 18, 15, 0, tzinfo=UTC),
        determination=Determination(
            result=DeterminationOutcome.ineligible,
            as_of_date=date(2026, 3, 17),
            rule_set_id="IL-2026",
            rule_set_version=1,
            engine_version="1",
            household_size=2,
            household_monthly_income=Decimal("3000.00"),
            monthly_income_limit=Decimal("2380.00"),
            rule_results=rules,
            missing_items=[],
        ),
    )


def test_FR_004_19_same_record_gives_the_same_explanation() -> None:
    record = _record()
    first = explain_determination(record, ExplanationAudience.caseworker)
    assert explain_determination(record, ExplanationAudience.caseworker) == first
    assert first.built_at == record.recorded_at
    assert first.determination_id == record.determination_id


def test_FR_004_10_applicant_text_covers_only_rules_not_met() -> None:
    record = _record()
    applicant = explain_determination(record, ExplanationAudience.applicant)
    caseworker = explain_determination(record, ExplanationAudience.caseworker)
    assert "ELIG-06" in applicant.text and "ELIG-01" not in applicant.text
    assert all(rule.value in caseworker.text for rule in RuleId)


def test_FR_004_18_rule_without_a_citation_says_so() -> None:
    explanation = explain_determination(_record(), ExplanationAudience.caseworker)
    assert NO_CITATION in explanation.text
    assert explanation.citations == []
