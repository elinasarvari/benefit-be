"""Effective-dated rule sets held as versioned code (ADR-003-01).

| Rule set        | Effective                  | Base annual | Per additional person |
|-----------------|----------------------------|-------------|-----------------------|
| IL-ADULT-2025 v1 | 2025-01-01 to 2025-12-31 | $15,000     | $5,000                |
| IL-ADULT-2026 v1 | 2026-01-01 to 2026-12-31 | $15,500     | $5,200                |

Both use 138% of the poverty guideline, ages 19-64, state IL. Adding a year means adding an entry;
never edit a published version (a change is a new `version`). Windows are inclusive on both ends.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from packages.domain.eligibility import PovertyGuideline, RuleId, RuleSet

POLICY_REFS: dict[RuleId, str] = {
    RuleId.ELIG_01: "TM-2.1",
    RuleId.ELIG_02: "TM-2.2",
    RuleId.ELIG_03: "TM-2.3",
    RuleId.ELIG_04: "TM-2.4",
    RuleId.ELIG_05: "TM-2.5",
    RuleId.ELIG_06: "TM-3.1",
}


def _il_adult(year: int, base: str, per_additional: str) -> RuleSet:
    return RuleSet(
        rule_set_id=f"IL-ADULT-{year}",
        version=1,
        program_year=year,
        effective_from=date(year, 1, 1),
        effective_to=date(year, 12, 31),
        state="IL",
        age_min=19,
        age_max=64,
        fpl_percent=138,
        poverty_guideline=PovertyGuideline(
            base_annual=Decimal(base), per_additional_annual=Decimal(per_additional)
        ),
        policy_refs=dict(POLICY_REFS),
    )


RULE_SETS: tuple[RuleSet, ...] = (
    _il_adult(2025, "15000", "5000"),
    _il_adult(2026, "15500", "5200"),
)


def rule_set_for(as_of_date: date) -> RuleSet | None:
    for rule_set in RULE_SETS:
        if rule_set.effective_from <= as_of_date <= rule_set.effective_to:
            return rule_set
    return None
