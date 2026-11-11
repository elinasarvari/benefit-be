from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from packages.domain.review import (
    AI_OVERRIDES,
    ALLOWED_ACTIONS,
    BLOCKING,
    REASON_REQUIRED,
    ActionType,
    CaseworkerActionSignal,
    ReviewReason,
)

A = ActionType


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        (
            ReviewReason.adverse_determination,
            {A.approve_denial, A.override_to_approve, A.request_information},
        ),
        (ReviewReason.cannot_determine, {A.request_information, A.correct_value}),
        (ReviewReason.notice_approval, {A.approve, A.edit_and_approve}),
        (
            ReviewReason.verification_review,
            {A.accept_findings, A.correct_value, A.dismiss_finding, A.request_information},
        ),
        (
            ReviewReason.missed_response_deadline,
            {A.deny_missing_information, A.extend_15_days, A.continue_with_held_data},
        ),
        (ReviewReason.explanation_review, {A.approve, A.edit_and_approve}),
    ],
)
def test_FR_010_01_allowed_actions_table_matches_spec(
    reason: ReviewReason, expected: set[ActionType]
) -> None:
    assert ALLOWED_ACTIONS[reason] == frozenset(expected)
    assert isinstance(ALLOWED_ACTIONS[reason], frozenset)


def test_FR_010_01_allowed_actions_covers_every_reason_with_16_pairs() -> None:
    assert set(ALLOWED_ACTIONS) == set(ReviewReason)
    assert sum(len(actions) for actions in ALLOWED_ACTIONS.values()) == 16


def test_FR_010_01_reason_required_ai_overrides_and_blocking_sets_match_spec() -> None:
    assert (
        frozenset(
            {
                A.override_to_approve,
                A.correct_value,
                A.dismiss_finding,
                A.extend_15_days,
                A.continue_with_held_data,
            }
        )
        == REASON_REQUIRED
    )
    assert frozenset({A.correct_value, A.dismiss_finding}) == AI_OVERRIDES
    assert frozenset(set(ReviewReason) - {ReviewReason.explanation_review}) == BLOCKING
    assert ReviewReason.explanation_review not in BLOCKING


def _signal(**overrides: Any) -> dict[str, Any]:
    return {
        "action_id": uuid4(),
        "task_id": uuid4(),
        "action": "override_to_approve",
        "reviewer_id": uuid4(),
        "reviewer_role": "caseworker",
        "reason": "Synthetic reason for test",
        "submitted_at": datetime(2026, 1, 5, 12, 0, tzinfo=UTC),
        **overrides,
    }


@pytest.mark.parametrize(("length", "valid"), [(0, False), (1, True), (500, True), (501, False)])
def test_FR_010_05_signal_reason_length_bounds_and_extra_fields_rejected(
    length: int, valid: bool
) -> None:
    payload = _signal(reason="x" * length)

    if valid:
        assert CaseworkerActionSignal(**payload).reason == "x" * length
    else:
        with pytest.raises(ValidationError):
            CaseworkerActionSignal(**payload)


def test_FR_010_05_signal_unknown_field_rejected() -> None:
    with pytest.raises(ValidationError):
        CaseworkerActionSignal(**_signal(applicant_name="Synthetic"))


def test_FR_010_05_signal_reason_may_be_absent_in_payload() -> None:
    signal = CaseworkerActionSignal(**_signal(action="approve", reason=None))

    assert signal.reason is None
    assert signal.corrected_value is None


def test_FR_010_05_signal_submitted_at_must_be_timezone_aware() -> None:
    with pytest.raises(ValidationError):
        CaseworkerActionSignal(**_signal(submitted_at=datetime(2026, 1, 5, 12, 0)))
