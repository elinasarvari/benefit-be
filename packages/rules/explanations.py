"""Decision explanations from a fixed template (FR-004-10, FR-004-19).

The text states the outcome, then each rule's result with its values and one citation from
`CITATIONS` (or "no citation available"), and names any missing information. The applicant text
covers the outcome and the unmet rules; the caseworker text covers every rule. Pure: the same
record and audience always give the same explanation, so `built_at` is the record's time, not a
clock. Names and IDs never appear in the text (VIII).
"""

from __future__ import annotations

from packages.domain.eligibility import Determination, DeterminationRecord, RuleOutcome, RuleResult
from packages.domain.explanation import Citation, DecisionExplanation, ExplanationAudience
from packages.rules.citations import CITATIONS

NO_CITATION = "no citation available"

_WORDING = {
    RuleOutcome.passed: "met",
    RuleOutcome.fail: "not met",
    RuleOutcome.missing: "could not be checked",
}


def _values(rule: RuleResult) -> str:
    parts = [f"{key} {value}" for key, value in sorted(rule.inputs.items()) if "." not in key]
    if rule.threshold is not None:
        parts.append(f"required {rule.threshold}")
    return f" ({', '.join(parts)})" if parts else ""


def _source(citation: Citation | None) -> str:
    if citation is None:
        return NO_CITATION
    return f"{citation.section} {citation.title}, manual version {citation.manual_version}"


def _rule_line(rule: RuleResult, citation: Citation | None) -> str:
    return (
        f"{rule.rule_id.value}: {_WORDING[rule.outcome]}{_values(rule)}. "
        f"Source: {_source(citation)}."
    )


def _summary(determination: Determination) -> str:
    line = f"Outcome: {determination.result.value}. Household size {determination.household_size}"
    if determination.household_monthly_income is not None:
        line += f", household monthly income {determination.household_monthly_income}"
    if determination.monthly_income_limit is not None:
        line += f", monthly income limit {determination.monthly_income_limit}"
    return line + "."


def _missing_line(determination: Determination) -> str | None:
    items = sorted({item.item for item in determination.missing_items})
    return f"Missing information: {', '.join(items)}." if items else None


def explain_determination(
    record: DeterminationRecord, audience: ExplanationAudience
) -> DecisionExplanation:
    determination = record.determination
    rules = determination.rule_results
    if audience is ExplanationAudience.applicant:
        rules = [rule for rule in rules if rule.outcome is not RuleOutcome.passed]
    lines = [_summary(determination)]
    if audience is ExplanationAudience.applicant and not rules:
        lines.append("Every rule was met.")
    lines += [_rule_line(rule, CITATIONS.get(rule.rule_id)) for rule in rules]
    missing = _missing_line(determination)
    if missing is not None:
        lines.append(missing)
    return DecisionExplanation(
        determination_id=record.determination_id,
        case_id=record.case_id,
        household_id=record.household_id,
        audience=audience,
        result=determination.result,
        text=" ".join(lines),
        citations=[CITATIONS[rule.rule_id] for rule in rules if rule.rule_id in CITATIONS],
        built_at=record.recorded_at,
    )
