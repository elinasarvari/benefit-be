"""Synthetic builders shared by the review queue tests (PR-3: generated IDs, no personal data)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from uuid import UUID

from packages.domain.auth import Role
from packages.domain.review import (
    ActionOutcome,
    ActionType,
    CaseworkerAction,
    CaseworkerActionSignal,
    CorrectedValue,
    EvidenceRef,
    RejectionCode,
    ReviewCause,
    ReviewReason,
    ReviewTask,
    ReviewTaskState,
    ReviewTaskStatus,
)

T0 = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)
DEADLINE = date(2026, 4, 16)
REASON_TEXT = "synthetic caseworker note 7f3a"


def task_state(
    reason: ReviewReason = ReviewReason.adverse_determination,
    *,
    status: ReviewTaskStatus = ReviewTaskStatus.open,
    evidence_refs: list[EvidenceRef] | None = None,
    household_id: UUID | None = None,
    case_id: UUID | None = None,
) -> ReviewTaskState:
    case = case_id or uuid.uuid4()
    return ReviewTaskState(
        task_id=uuid.uuid4(),
        household_id=household_id or uuid.uuid4(),
        case_id=case,
        workflow_id=f"case-{case}",
        reason=reason,
        cause=ReviewCause.ineligible,
        evidence_refs=evidence_refs or [],
        blocking=reason is not ReviewReason.explanation_review,
        status=status,
        opened_at_case=T0,
        opened_at_real=T0,
        decision_deadline=DEADLINE,
    )


def stored_task(**kwargs: object) -> ReviewTask:
    state = task_state(**kwargs)  # type: ignore[arg-type]
    return ReviewTask(**state.model_dump(), created_at=T0, updated_at=T0)


def action_signal(
    task_id: UUID,
    action: ActionType,
    *,
    reason: str | None = REASON_TEXT,
    action_id: UUID | None = None,
    finding_id: UUID | None = None,
    revision_id: UUID | None = None,
) -> CaseworkerActionSignal:
    return CaseworkerActionSignal(
        action_id=action_id or uuid.uuid4(),
        task_id=task_id,
        action=action,
        reviewer_id=uuid.uuid4(),
        reviewer_role=Role.caseworker,
        reason=reason,
        corrected_value=(
            CorrectedValue(field_ref="income.monthly", value=1200)
            if action is ActionType.correct_value
            else None
        ),
        finding_id=finding_id,
        revision_id=revision_id,
        submitted_at=T0,
    )


def recorded_action(
    task: ReviewTask | ReviewTaskState,
    action: ActionType = ActionType.approve_denial,
    *,
    outcome: ActionOutcome = ActionOutcome.applied,
    rejection_code: RejectionCode | None = None,
    action_id: UUID | None = None,
    finding_id: UUID | None = None,
) -> CaseworkerAction:
    signal = action_signal(task.task_id, action, action_id=action_id, finding_id=finding_id)
    return CaseworkerAction(
        **signal.model_dump(),
        household_id=task.household_id,
        case_id=task.case_id,
        outcome=outcome,
        rejection_code=rejection_code,
        applied_at_case=T0 if outcome is ActionOutcome.applied else None,
        recorded_at=T0,
    )
