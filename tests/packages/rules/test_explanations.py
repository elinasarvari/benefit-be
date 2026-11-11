"""Decision explanations from the fixed template (FR-004-10, FR-004-12, FR-004-18, FR-004-19)."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid5

import pytest

import packages.rules.explanations as explanations_module
from packages.domain.case import DeterminationOutcome
from packages.domain.eligibility import Determination, DeterminationRecord, RuleId, RuleOutcome
from packages.domain.explanation import ExplanationAudience
from packages.domain.household import HouseholdSnapshot
from packages.rules import evaluate
from packages.rules.citations import CITATIONS
from packages.rules.explanations import NO_CITATION, explain_determination

KNOWN_ANSWERS = Path(__file__).parents[3] / "db" / "seeds" / "data" / "known_answers.json"
ITEMS: dict[str, dict[str, Any]] = {
    item["case_key"]: item for item in json.loads(KNOWN_ANSWERS.read_text())
}
RECORDED_AT = datetime(2026, 3, 18, 15, 0, tzinfo=UTC)
NAMESPACE = UUID("00000000-0000-0000-0000-00000000a004")
BOTH = (ExplanationAudience.applicant, ExplanationAudience.caseworker)
ELIGIBLE = "KA-01-baseline-eligible"
INCOME_OVER = "KA-07-elig06-income-over-limit"
MISSING = "KA-08-elig03-citizenship-missing"
ONE_RULE_UNMET = "KA-02-elig01-out-of-state"


def determination_of(key: str) -> Determination:
    item = ITEMS[key]
    return evaluate(
        HouseholdSnapshot.model_validate(item["snapshot"]), date.fromisoformat(item["as_of_date"])
    )


def record_of(key: str) -> DeterminationRecord:
    return DeterminationRecord(
        determination_id=uuid5(NAMESPACE, f"determination-{key}"),
        household_id=uuid5(NAMESPACE, "household"),
        case_id=uuid5(NAMESPACE, "case"),
        snapshot_id=uuid5(NAMESPACE, "snapshot"),
        recorded_at=RECORDED_AT,
        determination=determination_of(key),
    )


def rule_line(text: str, rule_id: RuleId) -> str:
    [line] = re.findall(rf"{rule_id.value}: .*?(?= ELIG-\d+:| Missing information:|$)", text)
    return str(line)


def test_FR_004_10_01_income_ineligible_explanation_states_outcome_income_size_limit_and_cites_income_rule() -> (  # noqa: E501
    None
):
    record = record_of(INCOME_OVER)
    determination = record.determination
    explanation = explain_determination(record, ExplanationAudience.caseworker)

    assert "Outcome: ineligible" in explanation.text
    assert f"household monthly income {determination.household_monthly_income}" in explanation.text
    assert f"household size {determination.household_size}" in explanation.text.lower()
    assert f"monthly income limit {determination.monthly_income_limit}" in explanation.text
    assert CITATIONS[RuleId.ELIG_06] in explanation.citations
    assert "TM-3.1" in rule_line(explanation.text, RuleId.ELIG_06)


def test_FR_004_10_02_applicant_text_covers_outcome_and_unmet_rules_only() -> None:
    explanation = explain_determination(record_of(ONE_RULE_UNMET), ExplanationAudience.applicant)

    assert "Outcome: ineligible" in explanation.text
    assert "ELIG-01: not met" in explanation.text
    assert not any(rule.value in explanation.text for rule in RuleId if rule is not RuleId.ELIG_01)
    assert explanation.citations == [CITATIONS[RuleId.ELIG_01]]


def test_FR_004_10_03_caseworker_text_covers_every_rule_with_values_and_citation() -> None:
    explanation = explain_determination(record_of(ELIGIBLE), ExplanationAudience.caseworker)

    for rule_id in RuleId:
        line = rule_line(explanation.text, rule_id)
        assert f"{rule_id.value}: met" in line
        assert CITATIONS[rule_id].section in line
    assert "address_state IL" in explanation.text
    assert [c.rule_id for c in explanation.citations] == list(RuleId)


def test_FR_004_10_04_eligible_applicant_text_has_no_unmet_rules() -> None:
    explanation = explain_determination(record_of(ELIGIBLE), ExplanationAudience.applicant)

    assert "Outcome: eligible" in explanation.text
    assert "not met" not in explanation.text
    assert "could not be checked" not in explanation.text
    assert explanation.citations == []


def test_FR_004_10_05_rule_with_missing_information_names_the_missing_item() -> None:
    for audience in BOTH:
        explanation = explain_determination(record_of(MISSING), audience)

        assert "ELIG-03: could not be checked" in explanation.text
        assert "Missing information: citizenship_attested." in explanation.text


@pytest.mark.parametrize("key", [ELIGIBLE, INCOME_OVER, MISSING])
def test_FR_004_12_01_explanation_result_equals_determination_result_for_every_outcome(
    key: str,
) -> None:
    record = record_of(key)
    seen = {record_of(k).determination.result for k in (ELIGIBLE, INCOME_OVER, MISSING)}

    assert seen == set(DeterminationOutcome)
    for audience in BOTH:
        explanation = explain_determination(record, audience)
        assert explanation.result is record.determination.result
        assert f"Outcome: {record.determination.result.value}." in explanation.text


@pytest.mark.parametrize("key", list(ITEMS))
def test_FR_004_12_02_explanation_never_contradicts_rule_results(key: str) -> None:
    record = record_of(key)
    text = explain_determination(record, ExplanationAudience.caseworker).text

    for rule in record.determination.rule_results:
        line = rule_line(text, rule.rule_id)
        wording = {
            RuleOutcome.passed: f"{rule.rule_id.value}: met",
            RuleOutcome.fail: f"{rule.rule_id.value}: not met",
            RuleOutcome.missing: f"{rule.rule_id.value}: could not be checked",
        }[rule.outcome]
        assert line.startswith(wording)


@pytest.mark.parametrize("key", list(ITEMS))
@pytest.mark.parametrize("audience", BOTH)
def test_FR_004_19_02_every_number_in_text_matches_the_determination(
    key: str, audience: ExplanationAudience
) -> None:
    determination = determination_of(key)
    allowed_sources = [
        str(determination.household_size),
        str(determination.household_monthly_income),
        str(determination.monthly_income_limit),
    ]
    for rule in determination.rule_results:
        allowed_sources += [value for k, value in rule.inputs.items() if "." not in k]
        allowed_sources.append(rule.threshold or "")
    allowed = set(re.findall(r"\d+(?:\.\d+)?", " ".join(allowed_sources)))
    text = explain_determination(record_of(key), audience).text
    scrubbed = re.sub(r"ELIG-\d+|TM-\d+\.\d+|\d{4}-\d{2}-\d{2}", "", text)

    assert set(re.findall(r"\d+(?:\.\d+)?", scrubbed)) <= allowed


@pytest.mark.parametrize("key", [ELIGIBLE, INCOME_OVER, MISSING])
def test_FR_004_19_03_same_determination_gives_identical_explanation_twice(key: str) -> None:
    record = record_of(key)

    for audience in BOTH:
        assert explain_determination(record, audience) == explain_determination(record, audience)


def test_FR_004_19_04_built_at_equals_recorded_at_and_text_has_no_names_or_ids() -> None:
    record = record_of(INCOME_OVER)

    for audience in BOTH:
        explanation = explain_determination(record, audience)
        assert explanation.built_at == RECORDED_AT
        assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-", explanation.text)
        assert "SYNTH" not in explanation.text
        for identifier in (record.case_id, record.household_id, record.determination_id):
            assert str(identifier) not in explanation.text


def test_FR_004_18_03_rule_without_citation_entry_says_no_citation_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trimmed = MappingProxyType({k: v for k, v in CITATIONS.items() if k is not RuleId.ELIG_06})
    monkeypatch.setattr(explanations_module, "CITATIONS", trimmed)

    explanation = explain_determination(record_of(INCOME_OVER), ExplanationAudience.caseworker)

    assert f"Source: {NO_CITATION}." in rule_line(explanation.text, RuleId.ELIG_06)
    assert RuleId.ELIG_06 not in {c.rule_id for c in explanation.citations}
    assert "TM-3.1" not in explanation.text
