"""Citation table: each eligibility rule's manual section (FR-004-18).

The titles repeat the headings of `data/policy/training-manual.md`; a test keeps the two in step
(ADR-004-01). The package does no file I/O (KD-1). Explanations take their citations only from
this table, and say "no citation available" for a rule that has no entry (004 US4 scenario 3).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from types import MappingProxyType

from packages.domain.eligibility import RuleId
from packages.domain.explanation import Citation
from packages.rules.rule_sets import POLICY_REFS

MANUAL_VERSION = date(2026, 1, 1)

_TITLES: dict[RuleId, str] = {
    RuleId.ELIG_01: "Illinois residency",
    RuleId.ELIG_02: "Age requirement",
    RuleId.ELIG_03: "Citizenship attestation",
    RuleId.ELIG_04: "Identity verification",
    RuleId.ELIG_05: "Medicare entitlement",
    RuleId.ELIG_06: "Household income limit",
}

CITATIONS: Mapping[RuleId, Citation] = MappingProxyType(
    {
        rule_id: Citation(
            rule_id=rule_id,
            section=POLICY_REFS[rule_id],
            title=title,
            manual_version=MANUAL_VERSION,
        )
        for rule_id, title in _TITLES.items()
    }
)
