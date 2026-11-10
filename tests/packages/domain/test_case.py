from datetime import date
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from packages.domain.case import (
    SUBMISSION_NS,
    CaseState,
    CaseStatus,
    CaseworkerActionRequest,
    CaseworkerActionType,
    case_id_for,
    workflow_id_for,
)


def test_FR_001_01_case_id_for_same_reference_is_stable() -> None:
    first = case_id_for("sub-A")
    assert isinstance(first, UUID)
    assert case_id_for("sub-A") == first
    assert case_id_for("sub-B") != first
    assert first.version == 5
    assert SUBMISSION_NS != first
    assert workflow_id_for(first) == f"case-{first}"


def _status(days_remaining: int | None) -> CaseStatus:
    return CaseStatus(
        case_id=uuid4(),
        state=CaseState.decided if days_remaining is None else CaseState.verifying,
        received_on=date(2026, 3, 1),
        decision_deadline=date(2026, 4, 15),
        days_remaining=days_remaining,
    )


@pytest.mark.parametrize(
    ("days_remaining", "expected"),
    [(11, False), (10, True), (9, True), (0, True), (None, False)],
)
def test_FR_001_11_case_status_at_risk_at_ten_days_or_fewer(
    days_remaining: int | None, expected: bool
) -> None:
    assert _status(days_remaining).at_risk is expected


def _override(reason: str | None) -> CaseworkerActionRequest:
    return CaseworkerActionRequest(
        task_id=uuid4(),
        action=CaseworkerActionType.override_to_approve,
        reviewer_id=uuid4(),
        reason=reason,
        request_id=uuid4(),
    )


@pytest.mark.parametrize("reason", [None, ""])
def test_FR_001_20_override_request_requires_reason_up_to_500_chars(reason: str | None) -> None:
    with pytest.raises(ValidationError):
        _override(reason)


def test_FR_001_20_override_request_accepts_500_chars_rejects_501() -> None:
    assert _override("x" * 500).reason == "x" * 500
    with pytest.raises(ValidationError):
        _override("x" * 501)


def test_FR_001_20_approve_denial_needs_no_reason() -> None:
    request = CaseworkerActionRequest(
        task_id=uuid4(),
        action=CaseworkerActionType.approve_denial,
        reviewer_id=uuid4(),
        request_id=uuid4(),
    )
    assert request.reason is None
