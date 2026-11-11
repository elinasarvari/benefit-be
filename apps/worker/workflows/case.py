"""CaseWorkflow: one durable workflow per case, the only authority on its state (ADR-001-01, TW-3).

submitted -> verifying -> determining -> decided (approved), or -> under_review, where it waits on
the `caseworker_action` signal (TW-4, TW-5; ADR-001-04 fallback) until `approve_denial` decides it
denied or `override_to_approve` decides it approved. The review task and the caseworker actions
are 010's shapes (`packages/domain/review.py`, ADR-001-11) and the signal payload is 010's
`CaseworkerActionSignal`. Every change becomes one `CaseChange` with the
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
    from apps.worker.workflows.redetermination import PATCH_ID, RedeterminationPath
    from apps.worker.workflows.review import DETERMINATION_REF_KIND
    from packages.domain.case import (
        AllowedActions,
        CaseChange,
        CaseEvent,
        CaseEventKind,
        CaseOutcome,
        CaseOverride,
        CaseStart,
        CaseState,
        CaseStatus,
        CaseType,
        CaseUpdate,
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
    )
    from packages.domain.review import (
        BLOCKING,
        REASON_REQUIRED,
        ActionOutcome,
        ActionType,
        CaseworkerAction,
        CaseworkerActionSignal,
        EvidenceRef,
        RejectionCode,
        ReviewCause,
        ReviewReason,
        ReviewTask,
        ReviewTaskStatus,
    )
    from packages.domain.verification import VerificationRequest, VerificationResult
    from packages.domain.waits import ApplicantResponseSignal

# Reason codes of events that are not a caseworker action value (enum-backed, FR-001-10). A
# rejected action's event carries its `RejectionCode` value.
WORKFLOW_STARTED = "workflow_started"
VERIFICATION_COMPLETE = "verification_complete"


def parse_action(raw: dict[str, Any]) -> CaseworkerActionSignal | None:
    """Decode the signal payload; a malformed one is dropped. An action without the reason it
    needs still decodes, so it is rejected and recorded (FR-001-20, FR-010-05)."""
    try:
        return CaseworkerActionSignal.model_validate(raw)
    except ValidationError:
        return None


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
        self._pending: list[CaseworkerActionSignal] = []
        self._seen_actions: set[UUID] = set()
        self._responses: list[ApplicantResponseSignal] = []
        self._path: RedeterminationPath | None = None

    # --- Run ---------------------------------------------------------------------------------

    @workflow.run
    async def run(self, start: CaseStart) -> CaseStatus:
        # One patched branch: recorded application histories replay unchanged (TW-7, 007 R12).
        if start.case_type is CaseType.redetermination and workflow.patched(PATCH_ID):
            self._path = RedeterminationPath(self)
            await self._path.run()
            while self._pending:  # signals that arrived with the deciding one, recorded as rejected
                await self._reject(self._pending.pop(0), RejectionCode.task_closed)
            return self._status()
        await self._move(CaseState.verifying, WORKFLOW_STARTED)
        await workflow.execute_activity(
            "verify_documents",
            VerificationRequest(
                case_id=start.case_id,
                household_id=start.household_id,
                as_of_date=self._received_on,
            ),
            result_type=VerificationResult,
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
        request = parse_action(raw)
        if request is None:
            workflow.logger.warning("caseworker_action.malformed")
            return
        if request.action_id in self._seen_actions:
            return  # duplicate delivery (R10, FR-001-04, FR-010-06)
        self._seen_actions.add(request.action_id)
        self._pending.append(request)

    @workflow.signal(name="applicant_response")
    def applicant_response(self, payload: RawValue) -> None:
        raw = workflow.payload_converter().from_payload(payload.payload, dict)
        try:
            response = ApplicantResponseSignal.model_validate(raw)
        except ValidationError:
            workflow.logger.warning("applicant_response.malformed")
            return
        if self._state is not CaseState.awaiting_applicant:
            return  # nothing is waiting for it: not a request for information, or already answered
        self._responses.append(response)

    @workflow.query(name="status")
    def status(self) -> CaseStatus:
        return self._status()

    @workflow.query(name="allowed_actions")
    def allowed_actions(self) -> AllowedActions:
        task = self._open_task()
        if task is None:
            return AllowedActions()
        return AllowedActions(task_id=task.task_id, actions=allowed_actions_for(task.reason))

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
            pending_timer=self._path.pending_timer() if self._path is not None else None,
            open_review_task=(
                OpenReviewTask(
                    task_id=task.task_id,
                    reason=task.reason,
                    allowed_actions=allowed_actions_for(task.reason),
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
        """Apply the change to the workflow's own state, then write the projection.

        The workflow is the authority and MongoDB lags it by one activity (ADR-001-01), so the
        state moves when the change is decided, not when the write reports back. Otherwise a
        worker killed after the write commits but before it reports would resume one step behind
        the projection (FR-001-03, TC-75). The commands are unchanged, so replay is too."""
        change = CaseChange(
            case_id=self._start.case_id,
            household_id=self._start.household_id,
            seq=event.seq,
            event=event,
            **parts,
        )
        if event.to_state is not None:
            self._state = event.to_state
        await workflow.execute_activity("record_case_change", change, **UNTIL_DONE)

    async def _move(self, to_state: CaseState, reason_code: str) -> None:
        if not can_transition(self._state, to_state, self._start.case_type):
            # A programming error: fail the workflow task so a fixed worker can resume it.
            raise RuntimeError(f"illegal transition {self._state} -> {to_state}")
        event = self._event(CaseEventKind.state_changed, reason_code, to_state=to_state)
        await self._record(event, case_update=CaseUpdate(state=to_state))

    async def _open_review(self, determination: Determination) -> None:
        if determination.outcome is DeterminationOutcome.ineligible:
            reason, cause = ReviewReason.adverse_determination, ReviewCause.ineligible
        else:
            reason, cause = ReviewReason.cannot_determine, ReviewCause.cannot_determine
        task_id = workflow.uuid4()
        event = self._event(
            CaseEventKind.state_changed,
            determination.outcome.value,
            to_state=CaseState.under_review,
            review_task_id=task_id,
        )
        # 010's shape (ADR-001-11): evidence by reference only (010 R3); the failed rules and
        # missing items stay on the determination, which the workflow keeps.
        task = ReviewTask(
            task_id=task_id,
            household_id=self._start.household_id,
            case_id=self._start.case_id,
            workflow_id=workflow.info().workflow_id,
            reason=reason,
            cause=cause,
            evidence_refs=[
                EvidenceRef(kind=DETERMINATION_REF_KIND, id=determination.determination_id)
            ],
            blocking=reason in BLOCKING,
            status=ReviewTaskStatus.open,
            opened_at_case=event.case_time,
            opened_at_real=event.real_time,
            decision_deadline=self._deadline,
            created_at=event.real_time,
            updated_at=event.real_time,
        )
        self._task = task
        await self._record(
            event, case_update=CaseUpdate(state=CaseState.under_review), review_task=task
        )

    def _rejection(self, request: CaseworkerActionSignal) -> RejectionCode | None:
        task = self._open_task()
        if task is None or self._state is not CaseState.under_review:
            return RejectionCode.task_closed
        if request.task_id != task.task_id or request.action not in allowed_actions_for(
            task.reason
        ):
            return RejectionCode.not_allowed_for_reason
        if request.action in REASON_REQUIRED and not (request.reason and request.reason.strip()):
            return RejectionCode.reason_required
        return None

    async def _apply_next(self) -> None:
        request = self._pending.pop(0)
        rejection = self._rejection(request)
        if rejection is not None:
            await self._reject(request, rejection)
        elif request.action is ActionType.approve_denial:
            await self._decide_denied(request)
        else:
            await self._decide_approved(reason_code=request.action.value, request=request)

    def _action_record(
        self,
        request: CaseworkerActionSignal,
        event: CaseEvent,
        rejection: RejectionCode | None = None,
    ) -> CaseworkerAction:
        return CaseworkerAction(
            **request.model_dump(),
            household_id=self._start.household_id,
            case_id=self._start.case_id,
            outcome=ActionOutcome.applied if rejection is None else ActionOutcome.rejected,
            rejection_code=rejection,
            applied_at_case=event.case_time if rejection is None else None,
            recorded_at=event.real_time,
        )

    async def _reject(self, request: CaseworkerActionSignal, rejection: RejectionCode) -> None:
        task = self._open_task()
        event = self._event(
            CaseEventKind.action_rejected,
            rejection.value,
            actor_id=request.reviewer_id,
            review_task_id=task.task_id if task is not None else None,
        )
        action = self._action_record(request, event, rejection)
        await self._record(event, action=action)

    def _closed_task(self, request: CaseworkerActionSignal, real_time: datetime) -> ReviewTask:
        task = self._required_task()
        return task.model_copy(
            update={
                "status": ReviewTaskStatus.closed,
                "closed_by_action_id": request.action_id,
                "updated_at": real_time,
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
        self, *, reason_code: str, request: CaseworkerActionSignal | None = None
    ) -> None:
        event = self._event(
            CaseEventKind.state_changed,
            reason_code,
            to_state=CaseState.decided,
            actor_id=request.reviewer_id if request is not None else None,
            review_task_id=self._task.task_id if request is not None and self._task else None,
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
            parts["review_task"] = self._closed_task(request, event.real_time)
            parts["action"] = self._action_record(request, event)
        self._outcome = CaseOutcome.approved
        if "review_task" in parts:
            self._task = parts["review_task"]
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

    async def _decide_denied(self, request: CaseworkerActionSignal) -> None:
        task = self._required_task()
        determination = self._determination
        failed_rules: list[str] = []
        if determination is not None:
            failed_rules = [rule.value for rule in determination.failed_rules] or (
                [determination.reason_code.value] if determination.reason_code else []
            )
        denial_reason = denial_reason_for(task.reason, failed_rules)
        event = self._event(
            CaseEventKind.state_changed,
            request.action.value,
            to_state=CaseState.decided,
            actor_id=request.reviewer_id,
            review_task_id=task.task_id,
        )
        resolved = self._closed_task(request, event.real_time)
        self._outcome = CaseOutcome.denied
        self._denial_reason = denial_reason
        self._task = resolved
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
            action=self._action_record(request, event),
            notice=self._notice(NoticeType.denial, event.case_time),
        )
