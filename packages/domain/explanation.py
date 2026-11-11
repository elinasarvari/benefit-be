"""Decision explanation contract (004a, MVP): explanations built from a fixed template and a
citation table (FR-004-10, FR-004-18, FR-004-19). Pure models only (BA-6, BA-8).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from packages.domain.case import DeterminationOutcome
from packages.domain.eligibility import RuleId


class ExplanationAudience(StrEnum):
    applicant = "applicant"
    caseworker = "caseworker"


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: RuleId
    section: str = Field(min_length=1, max_length=20)
    title: str = Field(min_length=1, max_length=200)
    manual_version: date


class DecisionExplanation(BaseModel):
    """One audience's explanation, stored in `decision_explanations` as built (FR-004-13)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    determination_id: UUID
    case_id: UUID
    household_id: UUID
    audience: ExplanationAudience
    result: DeterminationOutcome
    text: str = Field(min_length=1, max_length=10_000)
    citations: list[Citation] = Field(default_factory=list)
    built_at: datetime
    synthetic: Literal[True] | None = None
