"""CaseWorkflow: one durable workflow per case, the only authority on its state (ADR-001-01, TW-3).

submitted -> verifying -> determining -> decided (approved), or -> under_review, where it waits on
the `caseworker_action` signal (TW-4, TW-5; ADR-001-04 fallback) until `approve_denial` decides it
denied or `override_to_approve` decides it approved. Every change becomes one `CaseChange` with the
next `seq`, written by `record_case_change` in one transaction. Times come only from
`workflow.now()` through the case clock (TW-1, ADR-001-03).

Signals are queued by the handler and applied one at a time by the run loop, so `seq` stays in
order and replay is deterministic.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid5

from temporalio import workflow
from temporalio.common import RawValue

with workflow.unsafe.imports_passed_through():
    from pydantic import ValidationError

    from apps.worker.policies import AGENT_STEP, UNTIL_DONE
    from apps.worker.workflows.calendar import (
        case_now_wf,
        case_today_wf,
        chicago_date,
        until_next_case_day,
    )
    from apps.worker.workflows.case_states import (
        allowed_actions_for,
        can_transition,
        coverage_for,
        decision_deadline,
        denial_reason_for,
    )
    from packages.domain.case import (
        ActionOutcome,
        AllowedActions,
        CaseChange,
        CaseEvent,
        CaseEventKind,
        CaseOutcome,
        CaseOverride,
        CaseStart,
        CaseState,
        CaseStatus,
        CaseUpdate,
        CaseworkerAction,
        CaseworkerActionRequest,
        CaseworkerActionType,
        CoveragePeriod,
        CoverageStatus,
        DenialReason,
        Determination,
        DeterminationOutcome,
        DeterminationRequest,
        EventActorType,
        NoticeRequest,
        NoticeType,
        OpenReviewTask,
        ReviewEvidence,
        ReviewReason,
        ReviewResolution,
        ReviewTask,
        ReviewTaskStatus,
        VerificationOutcome,
        VerificationRequest,
    )

# Reason codes of events that are not a caseworker action value (enum-backed, FR-001-10).
WORKFLOW_STARTED = "workflow_started"
VERIFICATION_COMPLETE = "verification_complete"
ACTION_NOT_ALLOWED = "action_not_allowed"
REASON_REQUIRED = "reason_required"
NO_OPEN_TASK = "no_open_task"


def parse_action(raw: dict[str, Any]) -> tuple[CaseworkerActionRequest | None, str | None]:
    """Decode the signal payload. An override without a reason is kept, so it can be rejected
    and recorded (FR-001-20) rather than dropped; any other malformed payload is dropped."""
    try:
        return CaseworkerActionRequest.model_validate(raw), None
    except ValidationError:
        pass
    try:
        checked = CaseworkerActionRequest.model_validate(
            {**raw, "action": CaseworkerActionType.approve_denial}
        )
        action = CaseworkerActionType(str(raw.get("action")))
    except (ValidationError, ValueError):
        return None, None
    return checked.model_copy(update={"action": action, "reason": None}), REASON_REQUIRED


@workflow.defn(name="CaseWorkflow")
class CaseWorkflow:
    @workflow.init
    def __init__(self, start: CaseStart) -> None:
        self._start = start
        self._state = CaseState.submitted
        self._seq = 0
        self._received_on: date = chicago_date(start.received_at)
        self._deadline: date = decision_deadline(self._received_on)
        self._outcome: CaseOutcome | None = None
        self._denial_reason: DenialReason | None = None
        self._determination: Determination | None = None
        self._task: ReviewTask | None = None
        self._pending: list[tuple[CaseworkerActionRequest, str | None]] = []
        self._seen_requests: set[UUID] = set()

    # --- Run ---------------------------------------------------------------------------------

    @workflow.run
    async def run(self, start: CaseStart) -> CaseStatus:
        await self._move(CaseState.verifying, WORKFLOW_STARTED)
        await workflow.execute_activity(
            "verify_documents",
            VerificationRequest(case_id=start.case_id, household_id=start.household_id),
            result_type=VerificationOutcome,
            **AGENT_STEP,
        )
        # 002, 010 and 011 add the needs-review and missing-document branches (TW-7: patched).
        await self._move(CaseState.determining, VERIFICATION_COMPLETE)
        # UNTIL_DONE: an outage is retried by the server, never fails the case (ADR-001-02).
        determination: Determination = await workflow.execute_activity(
            "determine_eligibility",
            DeterminationRequest(
                case_id=start.case_id,
                household_id=start.household_id,
                as_of_date=case_today_wf(start.clock),
            ),
            result_type=Determination,
            **UNTIL_DONE,
        )
        self._determination = determination

        if determination.outcome is DeterminationOutcome.eligible:
            await self._decide_approved(reason_code=determination.outcome.value)
        else:
            # FR-001-09, FR-001-16: every adverse or undetermined result waits on a person.
            await self._open_review(determination)
            while self._state is CaseState.under_review:
                try:
                    await workflow.wait_condition(
                        lambda: bool(self._pending), timeout=until_next_case_day(start.clock)
                    )
                except TimeoutError:
                    continue  # a new case day: wake so `status` counts days from now (R3)
                await self._apply_next()
        # Signals that arrived alongside the deciding one are still recorded, as rejected.
        while self._pending:
            await self._apply_next()
        return self._status()

    # --- Signals and queries -----------------------------------------------------------------

    @workflow.signal(name="caseworker_action")
    def caseworker_action(self, payload: RawValue) -> None:
        raw = workflow.payload_converter().from_payload(payload.payload, dict)
        request, problem = parse_action(raw)
        if request is None:
            workflow.logger.warning("caseworker_action.malformed")
            return
        if request.request_id in self._seen_requests:
            return  # duplicate delivery (R10, FR-001-04)
        self._seen_requests.add(request.request_id)
        self._pending.append((request, problem))

    @workflow.query(name="status")
    def status(self) -> CaseStatus:
        return self._status()

    @workflow.query(name="allowed_actions")
    def allowed_actions(self) -> AllowedActions:
        task = self._open_task()
        if task is None:
            return AllowedActions()
        return AllowedActions(task_id=task.id, actions=list(task.allowed_actions))

    # --- Steps -------------------------------------------------------------------------------

    def _open_task(self) -> ReviewTask | None:
        task = self._task
        return task if task is not None and task.status is ReviewTaskStatus.open else None

    def _required_task(self) -> ReviewTask:
        task = self._open_task()
        if task is None:  # _rejection already refused any action without an open task
            raise RuntimeError("no open review task")
        return task

    def _status(self) -> CaseStatus:
        days_remaining = None
        if self._state is not CaseState.decided:
            days_remaining = (self._deadline - case_today_wf(self._start.clock)).days
        task = self._open_task()
        return CaseStatus(
            case_id=self._start.case_id,
            state=self._state,
            received_on=self._received_on,
            decision_deadline=self._deadline,
            days_remaining=days_remaining,
            outcome=self._outcome,
            denial_reason=self._denial_reason,
            open_review_task=(
                OpenReviewTask(
                    task_id=task.id, reason=task.reason, allowed_actions=task.allowed_actions
                )
                if task is not None
                else None
            ),
        )

    def _event(
        self,
        kind: CaseEventKind,
        reason_code: str,
        *,
        to_state: CaseState | None = None,
        actor_id: UUID | None = None,
        review_task_id: UUID | None = None,
    ) -> CaseEvent:
        self._seq += 1
        real_time = workflow.now()
        return CaseEvent(
            id=workflow.uuid4(),
            case_id=self._start.case_id,
            household_id=self._start.household_id,
            seq=self._seq,
            kind=kind,
            from_state=self._state if to_state is not None else None,
            to_state=to_state,
            case_time=case_now_wf(self._start.clock),
            real_time=real_time,
            actor_type=EventActorType.user if actor_id is not None else EventActorType.system,
            actor_id=actor_id,
            reason_code=reason_code,
            review_task_id=review_task_id,
            created_at=real_time,
        )

    async def _record(self, event: CaseEvent, **parts: Any) -> None:
        change = CaseChange(
            case_id=self._start.case_id,
            household_id=self._start.household_id,
            seq=event.seq,
            event=event,
            **parts,
        )
        await workflow.execute_activity("record_case_change", change, **UNTIL_DONE)
        if event.to_state is not None:
            self._state = event.to_state

    async def _move(self, to_state: CaseState, reason_code: str) -> None:
        if not can_transition(self._state, to_state):
            # A programming error: fail the workflow task so a fixed worker can resume it.
            raise RuntimeError(f"illegal transition {self._state} -> {to_state}")
        event = self._event(CaseEventKind.state_changed, reason_code, to_state=to_state)
        await self._record(event, case_update=CaseUpdate(state=to_state))

    async def _open_review(self, determination: Determination) -> None:
        reason = (
            ReviewReason.adverse_determination
            if determination.outcome is DeterminationOutcome.ineligible
            else ReviewReason.cannot_determine
        )
        task_id = workflow.uuid4()
        event = self._event(
            CaseEventKind.state_changed,
            determination.outcome.value,
            to_state=CaseState.under_review,
            review_task_id=task_id,
        )
        task = ReviewTask(
            id=task_id,
            case_id=self._start.case_id,
            household_id=self._start.household_id,
            reason=reason,
            evidence=ReviewEvidence(
                determination_id=determination.determination_id,
                missing_items=list(determination.missing_items),
                failed_rules=[rule.value for rule in determination.failed_rules]
                or ([determination.reason_code.value] if determination.reason_code else []),
            ),
            allowed_actions=allowed_actions_for(reason),
            status=ReviewTaskStatus.open,
            opened_case_time=event.case_time,
            opened_real_time=event.real_time,
        )
        await self._record(
            event, case_update=CaseUpdate(state=CaseState.under_review), review_task=task
        )
        self._task = task

    def _rejection(self, request: CaseworkerActionRequest, problem: str | None) -> str | None:
        task = self._open_task()
        if task is None or self._state is not CaseState.under_review:
            return NO_OPEN_TASK
        if request.task_id != task.id or request.action not in task.allowed_actions:
            return ACTION_NOT_ALLOWED
        return problem

    async def _apply_next(self) -> None:
        request, problem = self._pending.pop(0)
        rejection = self._rejection(request, problem)
        if rejection is not None:
            await self._reject(request, rejection)
        elif request.action is CaseworkerActionType.approve_denial:
            await self._decide_denied(request)
        else:
            await self._decide_approved(reason_code=request.action.value, request=request)

    def _action_record(
        self, request: CaseworkerActionRequest, event: CaseEvent, outcome: ActionOutcome
    ) -> CaseworkerAction:
        return CaseworkerAction(
            id=request.request_id,
            case_id=self._start.case_id,
            household_id=self._start.household_id,
            task_id=request.task_id,
            action=request.action,
            reviewer_id=request.reviewer_id,
            reason=request.reason,
            outcome=outcome,
            case_time=event.case_time,
            real_time=event.real_time,
        )

    async def _reject(self, request: CaseworkerActionRequest, reason_code: str) -> None:
        task = self._open_task()
        event = self._event(
            CaseEventKind.action_rejected,
            reason_code,
            actor_id=request.reviewer_id,
            review_task_id=task.id if task is not None else None,
        )
        action = self._action_record(request, event, ActionOutcome.rejected)
        await self._record(event, action=action)

    def _resolved_task(self, request: CaseworkerActionRequest, case_time: datetime) -> ReviewTask:
        task = self._required_task()
        return task.model_copy(
            update={
                "status": ReviewTaskStatus.resolved,
                "resolution": ReviewResolution(
                    action=request.action,
                    reviewer_id=request.reviewer_id,
                    reason=request.reason,
                    case_time=case_time,
                ),
            }
        )

    def _notice(self, notice_type: NoticeType, case_time: datetime) -> NoticeRequest:
        return NoticeRequest(
            id=uuid5(self._start.case_id, notice_type.value),
            case_id=self._start.case_id,
            household_id=self._start.household_id,
            notice_type=notice_type,
            requested_case_time=case_time,
        )

    async def _decide_approved(
        self, *, reason_code: str, request: CaseworkerActionRequest | None = None
    ) -> None:
        event = self._event(
            CaseEventKind.state_changed,
            reason_code,
            to_state=CaseState.decided,
            actor_id=request.reviewer_id if request is not None else None,
            review_task_id=self._task.id if request is not None and self._task else None,
        )
        start, end = coverage_for(self._received_on)
        override = None
        parts: dict[str, Any] = {}
        if request is not None:
            override = CaseOverride(
                reviewer_id=request.reviewer_id,
                reason=request.reason or "",
                determination_id=(
                    self._determination.determination_id if self._determination else None
                ),
            )
            parts["review_task"] = self._resolved_task(request, event.case_time)
            parts["action"] = self._action_record(request, event, ActionOutcome.applied)
        await self._record(
            event,
            case_update=CaseUpdate(
                state=CaseState.decided,
                open=False,
                outcome=CaseOutcome.approved,
                override=override,
                decided_on=chicago_date(event.case_time),
            ),
            coverage=CoveragePeriod(
                id=uuid5(self._start.case_id, "coverage"),
                household_id=self._start.household_id,
                case_id=self._start.case_id,
                start_date=start,
                end_date=end,
                status=CoverageStatus.active,
            ),
            notice=self._notice(NoticeType.approval, event.case_time),
            **parts,
        )
        self._outcome = CaseOutcome.approved
        if "review_task" in parts:
            self._task = parts["review_task"]

    async def _decide_denied(self, request: CaseworkerActionRequest) -> None:
        task = self._required_task()
        denial_reason = denial_reason_for(task.reason, task.evidence.failed_rules)
        event = self._event(
            CaseEventKind.state_changed,
            request.action.value,
            to_state=CaseState.decided,
            actor_id=request.reviewer_id,
            review_task_id=task.id,
        )
        resolved = self._resolved_task(request, event.case_time)
        await self._record(
            event,
            case_update=CaseUpdate(
                state=CaseState.decided,
                open=False,
                outcome=CaseOutcome.denied,
                denial_reason=denial_reason,
                decided_on=chicago_date(event.case_time),
            ),
            review_task=resolved,
            action=self._action_record(request, event, ActionOutcome.applied),
            notice=self._notice(NoticeType.denial, event.case_time),
        )
        self._outcome = CaseOutcome.denied
        self._denial_reason = denial_reason
        self._task = resolved
