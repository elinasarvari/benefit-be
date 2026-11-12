"""Request and response bodies of the 001 case endpoints (contracts/rest-api.openapi.yaml)."""

from __future__ import annotations

from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.domain.case import (
    REASON_MAX_LENGTH,
    ActionType,
    ApplicationSubmission,
    CaseDetail,
    CaseHistoryPage,
    CasePage,
    CaseState,
    CaseStatus,
    CaseSummary,
)
from packages.domain.review import CorrectedValue

__all__ = [
    "ApplicationSubmission",
    "CaseDetail",
    "CaseHistoryPage",
    "CasePage",
    "CaseState",
    "CaseStatus",
    "CaseSummary",
    "ReviewActionBody",
]

ReviewAction = Literal[
    ActionType.approve_denial,
    ActionType.override_to_approve,
    # 002 (ADR-002-06): additive, each needs a reason
    ActionType.correct_value,
    ActionType.dismiss_finding,
]

REASON_REQUIRED_ACTIONS = frozenset(
    {ActionType.override_to_approve, ActionType.correct_value, ActionType.dismiss_finding}
)


class ReviewActionBody(BaseModel):
    """A caseworker's action. The reviewer comes from the session, never from the body.

    `reason` is never logged or echoed in an error (PL-11).
    """

    model_config = ConfigDict(extra="forbid")

    action: ReviewAction
    reason: str | None = Field(default=None, max_length=REASON_MAX_LENGTH, repr=False)
    request_id: UUID
    corrected_value: CorrectedValue | None = None
    finding_id: UUID | None = None

    @model_validator(mode="after")
    def _action_needs_its_fields(self) -> Self:
        if self.action in REASON_REQUIRED_ACTIONS and not (self.reason and self.reason.strip()):
            raise ValueError(f"{self.action.value} requires a reason")
        if self.action is ActionType.correct_value and self.corrected_value is None:
            raise ValueError("correct_value requires corrected_value")
        if self.action is ActionType.dismiss_finding and self.finding_id is None:
            raise ValueError("dismiss_finding requires finding_id")
        return self
