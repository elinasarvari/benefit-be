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
]


class ReviewActionBody(BaseModel):
    """A caseworker's action. The reviewer comes from the session, never from the body.

    `reason` is never logged or echoed in an error (PL-11).
    """

    model_config = ConfigDict(extra="forbid")

    action: ReviewAction
    reason: str | None = Field(default=None, max_length=REASON_MAX_LENGTH, repr=False)
    request_id: UUID

    @model_validator(mode="after")
    def _override_needs_reason(self) -> Self:
        if self.action is ActionType.override_to_approve and not (
            self.reason and self.reason.strip()
        ):
            raise ValueError("override_to_approve requires a reason")
        return self
