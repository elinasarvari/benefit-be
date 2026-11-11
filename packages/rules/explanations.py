"""Decision explanations from a fixed template (FR-004-10, FR-004-19).

Contract stub (MVP contracts PR): the text is the rule-by-rule trace (FR-003-05), which 011 uses
until 004a lands (FR-011-07). 004a replaces the body; the signature stays. Pure: the same record
and audience always give the same explanation, so `built_at` is the record's time, not a clock.
"""

from __future__ import annotations

from packages.domain.eligibility import DeterminationRecord, RuleOutcome
from packages.domain.explanation import DecisionExplanation, ExplanationAudience
from packages.rules.citations import CITATIONS

NO_CITATION = "no citation available"


def explain_determination(
    record: DeterminationRecord, audience: ExplanationAudience
) -> DecisionExplanation:
    determination = record.determination
    rules = determination.rule_results
    if audience is ExplanationAudience.applicant:
        rules = [rule for rule in rules if rule.outcome is not RuleOutcome.passed]
    lines = [f"Outcome: {determination.result.value}."]
    for rule in rules:
        citation = CITATIONS.get(rule.rule_id)
        source = f"section {citation.section}" if citation is not None else NO_CITATION
        lines.append(f"{rule.rule_id.value}: {rule.outcome.value} ({source}).")
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
