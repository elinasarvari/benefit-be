"""The workflow's own check of a caseworker action (FR-010-01, FR-010-05, ADR-010-02)."""

from __future__ import annotations

import uuid

import pytest

from apps.worker.workflows.review import REQUEST_REF_KIND, ReviewState, validate_action
from packages.domain.review import (
    ALLOWED_ACTIONS,
    REASON_REQUIRED,
    ActionType,
    EvidenceRef,
    RejectionCode,
    ReviewReason,
    ReviewTaskStatus,
)
from tests.apps.worker.review_support import action_signal, task_state

pytestmark = pytest.mark.unit

DISALLOWED = [
    (reason, action)
    for reason in ReviewReason
    for action in ActionType
    if action not in ALLOWED_ACTIONS[reason]
]
# Each * action with the first reason that allows it.
STAR_PAIRS = [
    (next(reason for reason in ReviewReason if action in ALLOWED_ACTIONS[reason]), action)
    for action in sorted(REASON_REQUIRED)
]


@pytest.mark.parametrize(("reason", "action"), DISALLOWED)
def test_FR_010_05_action_not_allowed_for_reason_refused(
    reason: ReviewReason, action: ActionType
) -> None:
    task = task_state(reason)

    code = validate_action(task, action_signal(task.task_id, action), ReviewState())

    assert len(DISALLOWED) == 6 * 11 - 16
    assert code is RejectionCode.not_allowed_for_reason


@pytest.mark.parametrize(("reason", "action"), STAR_PAIRS)
def test_FR_010_05_star_action_without_reason_refused(
    reason: ReviewReason, action: ActionType
) -> None:
    task = task_state(reason)

    code = validate_action(task, action_signal(task.task_id, action, reason=None), ReviewState())

    assert len(STAR_PAIRS) == 5
    assert code is RejectionCode.reason_required


def test_FR_010_01_second_extension_for_same_request_refused() -> None:
    request_id, other_request_id = uuid.uuid4(), uuid.uuid4()
    state = ReviewState(extended_request_ids={request_id})
    same = task_state(
        ReviewReason.missed_response_deadline,
        evidence_refs=[EvidenceRef(kind=REQUEST_REF_KIND, id=request_id)],
    )
    other = task_state(
        ReviewReason.missed_response_deadline,
        evidence_refs=[EvidenceRef(kind=REQUEST_REF_KIND, id=other_request_id)],
    )

    refused = validate_action(same, action_signal(same.task_id, ActionType.extend_15_days), state)
    allowed = validate_action(other, action_signal(other.task_id, ActionType.extend_15_days), state)

    assert refused is RejectionCode.extension_already_used
    assert allowed is None


def test_FR_010_05_action_on_closed_task_refused_task_closed() -> None:
    task = task_state(ReviewReason.adverse_determination, status=ReviewTaskStatus.closed)

    code = validate_action(
        task, action_signal(task.task_id, ActionType.approve_denial), ReviewState()
    )

    assert code is RejectionCode.task_closed


# Each action that needs its own field, with a reason that allows it and that field.
FIELD_PAIRS = [
    (ReviewReason.verification_review, ActionType.correct_value, "corrected_value"),
    (ReviewReason.verification_review, ActionType.dismiss_finding, "finding_id"),
    (ReviewReason.notice_approval, ActionType.edit_and_approve, "revision_id"),
]


@pytest.mark.parametrize(("reason", "action", "field"), FIELD_PAIRS)
def test_FR_010_05_action_without_its_required_field_refused(
    reason: ReviewReason, action: ActionType, field: str
) -> None:
    task = task_state(reason)
    complete = action_signal(
        task.task_id, action, finding_id=uuid.uuid4(), revision_id=uuid.uuid4()
    )
    missing = complete.model_copy(update={field: None})

    refused = validate_action(task, missing, ReviewState())
    allowed = validate_action(task, complete, ReviewState())

    assert refused is RejectionCode.missing_required_field
    assert allowed is None
