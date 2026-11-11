"""Interfaces the review loop uses from features not built yet, each with a stand-in
(plan "Open questions").

- 001: the case-state transition hook (`CaseTransitions`) and `case_events` recording.
- 011: the case clock (`CaseClock`) and the advance-clock signal (`AdvanceClock`).
- 002: the `verify_documents` and `draft_request` activities. 003: the `evaluate` activity.
- 007: the coverage end date, passed to `open_review(..., coverage_end=...)`.

When a feature lands, `CaseWorkflow` passes its real implementation instead and the payload models
below move to `packages/domain` with that feature's contracts (BA-8).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from temporalio import workflow

from packages.domain.review import ActionType, CorrectedValue, ReviewCause, ReviewReason

# Activity names owned by 002 and 003. Workflows call activities by name (TW-2).
VERIFY_DOCUMENTS = "verify_documents"
EVALUATE = "evaluate"
DRAFT_REQUEST = "draft_request"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- 001: case state transitions -------------------------------------------------------------


class CaseStep(StrEnum):
    """Where a caseworker action sends the case (FR-010-01 "Actions and result")."""

    decided_denied = "decided_denied"
    decided_approved = "decided_approved"
    determining = "determining"
    awaiting_applicant = "awaiting_applicant"
    request_drafted = "request_drafted"
    notice_sent = "notice_sent"
    explanation_approved = "explanation_approved"


class ReviewResult(_Model):
    """One applied caseworker action and what it changes. IDs and codes only (R3, PL-11)."""

    task_id: UUID
    action_id: UUID
    reason: ReviewReason
    action: ActionType
    step: CaseStep
    reviewer_id: UUID
    # Override to approve: the stored determination is never changed (R7, FR-001-20).
    override_action_id: UUID | None = None
    corrected_value: CorrectedValue | None = None
    dismissed_finding_id: UUID | None = None
    # Approve / edit and approve: the draft (notice or explanation) and the edited revision (R5).
    draft_ref: UUID | None = None
    revision_id: UUID | None = None
    # Request information: the drafted request. Extend by 15 days: the extended request.
    request_id: UUID | None = None
    # Deny for missing information on a redetermination: coverage ended by caseworker decision.
    coverage_ended_by_caseworker: bool = False


class CaseTransitions(Protocol):
    async def apply(self, result: ReviewResult) -> None: ...


class RecordedTransitions:
    """Stand-in for 001: keeps the results in workflow state. 001 moves the case and writes
    `case_events` (FR-001-10) here instead."""

    def __init__(self) -> None:
        self.results: list[ReviewResult] = []

    async def apply(self, result: ReviewResult) -> None:
        self.results.append(result)


# --- 011: case clock -----------------------------------------------------------------------------


class AdvanceClock(_Model):
    """Payload of the stand-in advance-clock signal (011 owns the real one, FR-011-05)."""

    days: float = Field(gt=0)


class CaseClock(Protocol):
    def now(self) -> datetime: ...

    def real_duration_until(self, case_instant: datetime) -> timedelta: ...


class StubCaseClock:
    """Stand-in for 011's case clock: case time runs `scale` times as fast as workflow time
    (1440 in the demo profile, one day per minute, FR-011-04) and can jump forward.

    Build it inside workflow code: it reads `workflow.now()` only.
    """

    def __init__(self, scale: float = 1.0) -> None:
        if scale <= 0:
            raise ValueError("scale must be positive")
        self._scale = scale
        self._anchor_real = workflow.now()
        self._anchor_case = self._anchor_real

    def now(self) -> datetime:
        return self._anchor_case + (workflow.now() - self._anchor_real) * self._scale

    def advance(self, delta: timedelta) -> None:
        self._anchor_case = self.now() + delta
        self._anchor_real = workflow.now()

    def real_duration_until(self, case_instant: datetime) -> timedelta:
        return max(timedelta(0), (case_instant - self.now()) / self._scale)


# --- 002 / 003: verification, drafting, determination --------------------------------------------


class CaseRef(_Model):
    household_id: UUID
    case_id: UUID


class VerificationFinding(_Model):
    finding_id: UUID
    cause: ReviewCause
    # The finding can be resolved by asking the applicant (automatic request, FR-010-04).
    missing_information: bool = False


class VerificationResult(_Model):
    findings: list[VerificationFinding] = Field(default_factory=list)


class EligibilityOutcome(StrEnum):
    eligible = "eligible"
    ineligible = "ineligible"
    cannot_determine = "cannot_determine"


class EvaluationResult(_Model):
    determination_id: UUID
    outcome: EligibilityOutcome


class RequestOrigin(StrEnum):
    automatic = "automatic"
    caseworker = "caseworker"


class DraftRequestInput(_Model):
    household_id: UUID
    case_id: UUID
    origin: RequestOrigin
    finding_ids: list[UUID] = Field(default_factory=list)
    action_id: UUID | None = None


class DraftRequestResult(_Model):
    request_id: UUID
