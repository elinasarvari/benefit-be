from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema

from packages.domain.case import DenialReason, DeterminationOutcome


class RuleId(StrEnum):
    ELIG_01 = "ELIG-01"
    ELIG_02 = "ELIG-02"
    ELIG_03 = "ELIG-03"
    ELIG_04 = "ELIG-04"
    ELIG_05 = "ELIG-05"
    ELIG_06 = "ELIG-06"


class RuleOutcome(StrEnum):
    passed = "pass"
    fail = "fail"
    missing = "missing"


class PovertyGuideline(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_annual: Decimal = Field(gt=0)
    per_additional_annual: Decimal = Field(ge=0)


class RuleSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_set_id: str
    version: int = Field(ge=1)
    program_year: int
    effective_from: date
    effective_to: date
    state: Literal["IL"]
    age_min: int
    age_max: int
    fpl_percent: int
    poverty_guideline: PovertyGuideline
    policy_refs: dict[RuleId, str]


class MissingItem(BaseModel):
    # Responses always carry both IDs (null when the item is not about one member or job).
    model_config = ConfigDict(
        extra="forbid", frozen=True, json_schema_serialization_defaults_required=True
    )

    item: str
    member_id: UUID | None = None
    job_id: UUID | None = None
    reason: str


class RuleResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: RuleId
    outcome: RuleOutcome
    inputs: dict[str, str]
    threshold: str | None
    policy_ref: str
    missing: list[MissingItem]


class Determination(BaseModel):
    """Engine output. `rule_results` are in rule-ID order."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    result: DeterminationOutcome
    as_of_date: date
    rule_set_id: str | None
    rule_set_version: int | None
    engine_version: str
    household_size: int = Field(ge=0)
    household_monthly_income: Decimal | None
    monthly_income_limit: Decimal | None
    rule_results: list[RuleResult]
    missing_items: list[MissingItem]


class DeterminationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    determination_id: UUID
    household_id: UUID
    case_id: UUID
    snapshot_id: UUID
    recorded_at: datetime
    determination: Determination


# The contract lists two reasons inline; a determination never gives the third `DenialReason`.
ReasonCode = Annotated[
    Literal[DenialReason.income_over_limit, DenialReason.other_rule_not_met] | None,
    WithJsonSchema(
        {
            "type": ["string", "null"],
            "enum": [
                DenialReason.income_over_limit.value,
                DenialReason.other_rule_not_met.value,
                None,
            ],
        }
    ),
]


class DeterminationView(BaseModel):
    """Read model of a stored determination for the case page (FR-003-23, ADR-003-05)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    determination_id: UUID
    result: DeterminationOutcome
    reason_code: ReasonCode
    as_of_date: date
    rule_set_id: str | None
    rule_set_version: int | None
    household_size: int = Field(ge=0)
    household_monthly_income: Decimal | None
    monthly_income_limit: Decimal | None
    rules: list[RuleResult] = Field(min_length=6, max_length=6)
    missing_items: list[MissingItem]
    recorded_at: datetime
