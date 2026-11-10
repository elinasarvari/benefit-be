"""Synthetic CaseChange builders for worker repository and activity tests (PR-3)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from packages.domain.case import (
    ActionOutcome,
    CaseChange,
    CaseClock,
    CaseEvent,
    CaseEventKind,
    CaseOutcome,
    CaseOverride,
    CaseState,
    CaseUpdate,
    CaseworkerAction,
    CaseworkerActionType,
    CoveragePeriod,
    CoverageStatus,
    EventActorType,
    NoticeRequest,
    NoticeType,
    ReviewEvidence,
    ReviewReason,
    ReviewTask,
    ReviewTaskStatus,
)

T0 = datetime(2026, 3, 17, 17, 0, tzinfo=UTC)
RECEIVED_ON = date(2026, 3, 17)
REVIEWER = uuid.UUID("00000000-0000-4000-8000-00000000c051")  # synthetic cw-synth-1
DETERMINATION_ID = uuid.UUID("00000000-0000-4000-8000-0000000000d1")
SYNTH_REASON = "SYNTH-REASON-123"


def new_ids() -> tuple[uuid.UUID, uuid.UUID]:
    return uuid.uuid4(), uuid.uuid4()


def case_document(case_id: uuid.UUID, household_id: uuid.UUID) -> dict[str, Any]:
    """The `cases` document the API inserts before starting the workflow (ADR-001-02)."""
    midnight = datetime(2026, 3, 17, tzinfo=UTC)
    return {
        "_id": case_id,
        "case_id": case_id,
        "household_id": household_id,
        "case_type": "application",
        "submission_reference": f"sub-{case_id}",
        "state": "submitted",
        "open": True,
        "received_on": midnight,
        "decision_deadline": midnight + timedelta(days=45),
        "clock": CaseClock(real_start=T0, case_start=T0, scale=1).model_dump(),
        "last_seq": 0,
        "created_at": T0,
        "updated_at": T0,
    }


def event(
    case_id: uuid.UUID,
    household_id: uuid.UUID,
    seq: int,
    kind: CaseEventKind = CaseEventKind.state_changed,
    from_state: CaseState | None = None,
    to_state: CaseState | None = None,
    reason_code: str = "workflow_started",
    actor_id: uuid.UUID | None = None,
    review_task_id: uuid.UUID | None = None,
) -> CaseEvent:
    at = T0 + timedelta(minutes=seq)
    return CaseEvent(
        id=uuid.uuid5(case_id, f"event-{seq}"),
        case_id=case_id,
        household_id=household_id,
        seq=seq,
        kind=kind,
        from_state=from_state,
        to_state=to_state,
        case_time=at,
        real_time=at,
        actor_type=EventActorType.user if actor_id else EventActorType.system,
        actor_id=actor_id,
        reason_code=reason_code,
        review_task_id=review_task_id,
        created_at=at,
    )


def move(
    case_id: uuid.UUID, household_id: uuid.UUID, seq: int, from_state: CaseState, to: CaseState
) -> CaseChange:
    return CaseChange(
        case_id=case_id,
        household_id=household_id,
        seq=seq,
        event=event(case_id, household_id, seq, from_state=from_state, to_state=to),
        case_update=CaseUpdate(state=to),
    )


def review_task(
    case_id: uuid.UUID, household_id: uuid.UUID, task_id: uuid.UUID, status: ReviewTaskStatus
) -> ReviewTask:
    return ReviewTask(
        id=task_id,
        case_id=case_id,
        household_id=household_id,
        reason=ReviewReason.adverse_determination,
        evidence=ReviewEvidence(
            determination_id=DETERMINATION_ID, failed_rules=["income_over_limit"]
        ),
        allowed_actions=[
            CaseworkerActionType.approve_denial,
            CaseworkerActionType.override_to_approve,
        ],
        status=status,
        opened_case_time=T0,
        opened_real_time=T0,
    )


def approved(
    case_id: uuid.UUID,
    household_id: uuid.UUID,
    seq: int,
    *,
    override_reason: str | None = None,
    task_id: uuid.UUID | None = None,
) -> CaseChange:
    """The deciding change: approved, with coverage and notice; an override if a reason is given."""
    actor = REVIEWER if override_reason else None
    ev = event(
        case_id,
        household_id,
        seq,
        from_state=CaseState.under_review if override_reason else CaseState.determining,
        to_state=CaseState.decided,
        reason_code="override_to_approve" if override_reason else "eligible",
        actor_id=actor,
        review_task_id=task_id,
    )
    parts: dict[str, Any] = {}
    override = None
    if override_reason is not None and task_id is not None:
        override = CaseOverride(
            reviewer_id=REVIEWER, reason=override_reason, determination_id=DETERMINATION_ID
        )
        parts["review_task"] = review_task(
            case_id, household_id, task_id, ReviewTaskStatus.resolved
        )
        parts["action"] = CaseworkerAction(
            id=uuid.uuid5(case_id, "request-1"),
            case_id=case_id,
            household_id=household_id,
            task_id=task_id,
            action=CaseworkerActionType.override_to_approve,
            reviewer_id=REVIEWER,
            reason=override_reason,
            outcome=ActionOutcome.applied,
            case_time=ev.case_time,
            real_time=ev.real_time,
        )
    return CaseChange(
        case_id=case_id,
        household_id=household_id,
        seq=seq,
        event=ev,
        case_update=CaseUpdate(
            state=CaseState.decided,
            open=False,
            outcome=CaseOutcome.approved,
            override=override,
            decided_on=RECEIVED_ON,
        ),
        coverage=CoveragePeriod(
            id=uuid.uuid5(case_id, "coverage"),
            household_id=household_id,
            case_id=case_id,
            start_date=date(2026, 3, 1),
            end_date=date(2027, 2, 28),
            status=CoverageStatus.active,
        ),
        notice=NoticeRequest(
            id=uuid.uuid5(case_id, NoticeType.approval.value),
            case_id=case_id,
            household_id=household_id,
            notice_type=NoticeType.approval,
            requested_case_time=ev.case_time,
        ),
        **parts,
    )
