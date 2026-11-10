from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from packages.domain.eligibility import RuleId, RuleOutcome
from packages.domain.household import HouseholdSnapshot
from packages.rules import evaluate

KNOWN_ANSWERS = Path(__file__).parents[3] / "db" / "seeds" / "data" / "known_answers.json"
ITEMS: list[dict[str, Any]] = json.loads(KNOWN_ANSWERS.read_text())


@pytest.mark.parametrize("item", ITEMS, ids=[i["case_key"] for i in ITEMS])
def test_FR_003_09_known_answer_case_matches_expected_result_failed_missing_income_limit(
    item: dict[str, Any],
) -> None:
    household = HouseholdSnapshot.model_validate(item["snapshot"])

    result = evaluate(household, date.fromisoformat(item["as_of_date"]))

    assert result.result.value == item["expected_result"]
    failed = [r.rule_id.value for r in result.rule_results if r.outcome is RuleOutcome.fail]
    assert failed == item["expected_failed"]
    assert [m.item for m in result.missing_items] == item["expected_missing"]
    income = item["expected_income"]
    assert result.household_monthly_income == (None if income is None else Decimal(income))
    limit = item["expected_limit"]
    assert result.monthly_income_limit == (None if limit is None else Decimal(limit))
    trace = item.get("expected_trace")
    if trace:
        elig06 = next(r for r in result.rule_results if r.rule_id is RuleId.ELIG_06)
        assert elig06.threshold == trace["threshold"]
        assert elig06.policy_ref == trace["policy_ref"]
        for key in ("household_monthly_income", "household_size", "program_year"):
            assert elig06.inputs[key] == trace[key]


def test_FR_003_09_known_answers_have_30_cases_covering_every_rule_edge_case_and_year() -> None:
    covers = {label for item in ITEMS for label in item["covers"]}
    keys = [item["case_key"] for item in ITEMS]
    required = {
        f"{rule.value}:{outcome}" for rule in RuleId for outcome in ("pass", "fail", "missing")
    }
    required |= {
        "edge:income-at-limit",
        "edge:turns-19-or-65",
        "edge:size-change",
        "edge:no-rule-set",
        "edge:zero-income",
        "edge:fail-plus-missing",
        "edge:weekly-400",
        "frequency:weekly",
        "frequency:biweekly",
        "frequency:semimonthly",
        "frequency:monthly",
        "year:2025",
        "year:2026",
        "multiple-jobs",
    }

    assert len(ITEMS) >= 30
    assert len(keys) == len(set(keys))
    assert required <= covers, sorted(required - covers)
