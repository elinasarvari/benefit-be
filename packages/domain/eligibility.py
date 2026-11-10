from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


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


class EligibilityResult(StrEnum):
    eligible = "eligible"
    ineligible = "ineligible"
    cannot_determine = "cannot_determine"


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
    model_config = ConfigDict(extra="forbid", frozen=True)

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
    missing: list[MissingItem] = []


class Determination(BaseModel):
    """Engine output. `rule_results` are in rule-ID order."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    result: EligibilityResult
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
