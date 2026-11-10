"""Review queue contracts (feature 010): reasons, actions, the action table and payloads.

Shared by Workflows, Platform & UI and Knowledge & data (BA-8). Pure Pydantic models and
data tables only (BA-6).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from packages.domain.auth import Role


class ReviewReason(StrEnum):
    adverse_determination = "adverse_determination"
    cannot_determine = "cannot_determine"
    notice_approval = "notice_approval"
    verification_review = "verification_review"
    missed_response_deadline = "missed_response_deadline"
    explanation_review = "explanation_review"


class ActionType(StrEnum):
    approve_denial = "approve_denial"
    override_to_approve = "override_to_approve"
    request_information = "request_information"
    correct_value = "correct_value"
    dismiss_finding = "dismiss_finding"
    accept_findings = "accept_findings"
    approve = "approve"
    edit_and_approve = "edit_and_approve"
    deny_missing_information = "deny_missing_information"
    extend_15_days = "extend_15_days"
    continue_with_held_data = "continue_with_held_data"


class ReviewCause(StrEnum):
    low_confidence = "low_confidence"
    unreadable_document = "unreadable_document"
    different_person = "different_person"
    identity_mismatch = "identity_mismatch"
    income_source_mismatch = "income_source_mismatch"
    missing_citation = "missing_citation"
    verification_unavailable = "verification_unavailable"
    retry_limit_reached = "retry_limit_reached"
    non_retryable_error = "non_retryable_error"
    determination_failed = "determination_failed"
    ineligible = "ineligible"
    cannot_determine = "cannot_determine"
    notice_ready = "notice_ready"
    response_deadline_missed = "response_deadline_missed"
    explanation_check_failed = "explanation_check_failed"


class ReviewTaskStatus(StrEnum):
    open = "open"
    closed = "closed"


class ReviewFlag(StrEnum):
    reminder = "reminder"
    escalated = "escalated"


class ActionOutcome(StrEnum):
    applied = "applied"
    rejected = "rejected"


class RejectionCode(StrEnum):
    not_allowed_for_reason = "not_allowed_for_reason"
    reason_required = "reason_required"
    task_closed = "task_closed"
    extension_already_used = "extension_already_used"
    # The action's own field is missing (corrected value, finding or revision).
    missing_required_field = "missing_required_field"
    # Request information: drafting the request failed, so the task stays open.
    request_draft_failed = "request_draft_failed"


_A = ActionType

# FR-010-01 action table: the actions a caseworker may take for each reason.
ALLOWED_ACTIONS: Mapping[ReviewReason, frozenset[ActionType]] = MappingProxyType(
    {
        ReviewReason.adverse_determination: frozenset(
            {_A.approve_denial, _A.override_to_approve, _A.request_information}
        ),
        ReviewReason.cannot_determine: frozenset({_A.request_information, _A.correct_value}),
        ReviewReason.notice_approval: frozenset({_A.approve, _A.edit_and_approve}),
        ReviewReason.verification_review: frozenset(
            {_A.accept_findings, _A.correct_value, _A.dismiss_finding, _A.request_information}
        ),
        ReviewReason.missed_response_deadline: frozenset(
            {_A.deny_missing_information, _A.extend_15_days, _A.continue_with_held_data}
        ),
        ReviewReason.explanation_review: frozenset({_A.approve, _A.edit_and_approve}),
    }
)

# Actions that need a written reason from the caseworker.
REASON_REQUIRED: frozenset[ActionType] = frozenset(
    {
        _A.override_to_approve,
        _A.correct_value,
        _A.dismiss_finding,
        _A.extend_15_days,
        _A.continue_with_held_data,
    }
)

# Actions that override an AI result and are stored as eval overrides (R6).
AI_OVERRIDES: frozenset[ActionType] = frozenset({_A.correct_value, _A.dismiss_finding})

# Reasons whose open task blocks the decision (or the notice).
BLOCKING: frozenset[ReviewReason] = frozenset(set(ReviewReason) - {ReviewReason.explanation_review})

ReasonText = Annotated[str, StringConstraints(min_length=1, max_length=500)]


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceRef(_Contract):
    """A pointer to evidence. Only a kind and an ID, never the content (R3)."""

    kind: str
    id: UUID


class CorrectedValue(_Contract):
    field_ref: str
    value: str | int | float | bool | None


class ReviewTaskState(_Contract):
    """The in-workflow view of a review task."""

    task_id: UUID
    household_id: UUID
    case_id: UUID
    workflow_id: str
    reason: ReviewReason
    cause: ReviewCause
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    blocking: bool
    status: ReviewTaskStatus = ReviewTaskStatus.open
    opened_at_case: AwareDatetime
    opened_at_real: AwareDatetime
    reminder_at: AwareDatetime | None = None
    escalated_at: AwareDatetime | None = None
    flags: list[ReviewFlag] = Field(default_factory=list)
    decision_deadline: date
    coverage_end: date | None = None
    closed_by_action_id: UUID | None = None


class ReviewTask(ReviewTaskState):
    """A review task as stored in `review_tasks` (projection, ADR-010-01)."""

    created_at: AwareDatetime
    updated_at: AwareDatetime


class CaseworkerActionSignal(_Contract):
    """Payload of the `caseworker_action` signal (TW-5, ADR-010-02).

    `reason` is free text written by staff: never log it (PL-11). It is required for the
    actions in REASON_REQUIRED; the workflow refuses those without it (`reason_required`).
    """

    action_id: UUID
    task_id: UUID
    action: ActionType
    reviewer_id: UUID
    reviewer_role: Role
    reason: ReasonText | None = None
    corrected_value: CorrectedValue | None = None
    finding_id: UUID | None = None
    revision_id: UUID | None = None
    submitted_at: AwareDatetime


class CaseworkerAction(CaseworkerActionSignal):
    """A caseworker action as stored in `caseworker_actions`, applied or rejected."""

    household_id: UUID
    case_id: UUID
    outcome: ActionOutcome
    rejection_code: RejectionCode | None = None
    applied_at_case: AwareDatetime | None = None
    recorded_at: AwareDatetime
