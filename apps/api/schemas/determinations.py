"""Response bodies of the determinations endpoint (specs/003-eligibility-rules/contracts)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from packages.domain.eligibility import DeterminationView, MissingItem, RuleResult

__all__ = ["DeterminationList", "DeterminationView", "MissingItem", "RuleResult"]

MAX_DETERMINATIONS = 20


class DeterminationList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[DeterminationView] = Field(max_length=MAX_DETERMINATIONS)
