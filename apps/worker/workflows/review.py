"""The review loop a case workflow composes (feature 010; contracts/workflow-review.md).

`CaseWorkflow` (001) owns each review task as a durable wait and projects it to `review_tasks`
(ADR-010-01). Caseworker actions arrive on one `caseworker_action` signal, are queued, and are
validated here against the shared action table (ADR-010-02). Reminder and escalation are timers on
the case clock (ADR-010-03). Verification and determination failures past their retry limit open a
task instead of failing the case (ADR-010-04).

Workflow code only: no DB, agent or package I/O (TW-1, TW-2). Every write is an activity. Nothing
logged here carries a free-text reason, a corrected value or applicant data (PL-11).
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from types import MappingProxyType
from typing import TypeVar
from uuid import UUID

from pydantic import BaseModel
from temporalio import workflow
from temporalio.exceptions import ActivityError, CancelledError, RetryState

with workflow.unsafe.imports_passed_through():
    from apps.worker.policies import AGENT_STEP, DEFAULT, RULES_STEP
    from apps.worker.review_payloads import ReviewFlagUpdate, ReviewTaskClose
    from apps.worker.workflows.review_ports import (
        DRAFT_REQUEST,
        EVALUATE,
        VERIFY_DOCUMENTS,
        CaseClock,
        CaseRef,
        CaseStep,
        CaseTransitions,
        DraftRequestInput,
        DraftRequestResult,
        EvaluationResult,
        RequestOrigin,
        ReviewResult,
        VerificationFinding,
        VerificationResult,
    )
    from apps.worker.workflows.review_timers import ReviewWindows, escalation_due, reminder_due
    from packages.domain.review import (
        ALLOWED_ACTIONS,
        BLOCKING,
        REASON_REQUIRED,
        ActionOutcome,
        ActionType,
        CaseworkerAction,
        CaseworkerActionSignal,
        EvidenceRef,
        RejectionCode,
        ReviewCause,
        ReviewFlag,
        ReviewReason,
        ReviewTask,
        ReviewTaskState,
        ReviewTaskStatus,
    )

# ADR-010-05 (Accepted, option A): a failed determination opens "Cannot determine".
DETERMINATION_FAILURE_REASON = ReviewReason.cannot_determine
DETERMINATION_FAILURE_CAUSE = ReviewCause.determination_failed

# Evidence reference kinds (research R3).
REQUEST_REF_KIND = "request_id"
FINDING_REF_KIND = "finding_id"
DETERMINATION_REF_KIND = "determination_id"
NOTICE_DRAFT_REF_KIND = "notice_draft_id"
EXPLANATION_REF_KIND = "explanation_id"
DRAFT_REF_KINDS = frozenset({NOTICE_DRAFT_REF_KIND, EXPLANATION_REF_KIND})

# A flag counts as due this close to its instant, so float scaling can never leave a 0 s timer.
DUE_TOLERANCE = timedelta(seconds=1)
MIN_WAIT = timedelta(seconds=1)

# A projection write (`review_tasks`, `caseworker_actions`) that fails past its preset's retries is
# tried again, waiting 1 minute and doubling up to 1 hour, so a long outage adds few history events.
PROJECTION_RETRY_WAIT = timedelta(minutes=1)
PROJECTION_RETRY_MAX_WAIT = timedelta(hours=1)


class ProjectionRejected(RuntimeError):
    """A projection write failed with an error no retry can fix.

    Not a Temporal failure type, so raising it fails the workflow *task*, not the case: Temporal
    retries the task without adding history, and the case resumes once a fix is deployed (TW-11).
    """


_A = ActionType
_R = ReviewReason

# FR-010-01 "Actions and result": where each allowed (reason, action) sends the case.
RESULTS: Mapping[tuple[ReviewReason, ActionType], CaseStep] = MappingProxyType(
    {
        (_R.adverse_determination, _A.approve_denial): CaseStep.decided_denied,
        (_R.adverse_determination, _A.override_to_approve): CaseStep.decided_approved,
        (_R.adverse_determination, _A.request_information): CaseStep.request_drafted,
        (_R.cannot_determine, _A.request_information): CaseStep.request_drafted,
        (_R.cannot_determine, _A.correct_value): CaseStep.determining,
        (_R.notice_approval, _A.approve): CaseStep.notice_sent,
        (_R.notice_approval, _A.edit_and_approve): CaseStep.notice_sent,
        (_R.verification_review, _A.accept_findings): CaseStep.determining,
        (_R.verification_review, _A.correct_value): CaseStep.determining,
        (_R.verification_review, _A.dismiss_finding): CaseStep.determining,
        (_R.verification_review, _A.request_information): CaseStep.request_drafted,
        (_R.missed_response_deadline, _A.deny_missing_information): CaseStep.decided_denied,
        (_R.missed_response_deadline, _A.extend_15_days): CaseStep.awaiting_applicant,
        (_R.missed_response_deadline, _A.continue_with_held_data): CaseStep.determining,
        (_R.explanation_review, _A.approve): CaseStep.explanation_approved,
        (_R.explanation_review, _A.edit_and_approve): CaseStep.explanation_approved,
    }
)

# The field each action needs in its signal (FR-010-01: the corrected value, the dismissed finding,
# the edited revision). Without it the action is refused (FR-010-05).
REQUIRED_FIELDS: Mapping[ActionType, str] = MappingProxyType(
    {
        _A.correct_value: "corrected_value",
        _A.dismiss_finding: "finding_id",
        _A.edit_and_approve: "revision_id",
    }
)

ResultT = TypeVar("ResultT", bound=BaseModel)
HostT = TypeVar("HostT")


@dataclass(frozen=True)
class ReviewContext:
    household_id: UUID
    case_id: UUID
    workflow_id: str
    decision_deadline: date


@dataclass
class ReviewState:
    """Durable review state, rebuilt by replay (research R1, R2)."""

    tasks: dict[UUID, ReviewTaskState] = field(default_factory=dict)
    seen_action_ids: set[UUID] = field(default_factory=set)
    auto_rfi_used: bool = False
    extended_request_ids: set[UUID] = field(default_factory=set)


def ref_of(task: ReviewTaskState, kinds: frozenset[str]) -> UUID | None:
    return next((ref.id for ref in task.evidence_refs if ref.kind in kinds), None)


def validate_action(
    task: ReviewTaskState, signal: CaseworkerActionSignal, state: ReviewState
) -> RejectionCode | None:
    """Why the workflow refuses this action, or None when it applies (FR-010-01, FR-010-05)."""
    if task.status is not ReviewTaskStatus.open:
        return RejectionCode.task_closed
    if signal.action not in ALLOWED_ACTIONS[task.reason]:
        return RejectionCode.not_allowed_for_reason
    if signal.action in REASON_REQUIRED and not signal.reason:
        return RejectionCode.reason_required
    required = REQUIRED_FIELDS.get(signal.action)
    if required is not None and getattr(signal, required) is None:
        return RejectionCode.missing_required_field
    if signal.action is ActionType.extend_15_days:
        request_id = ref_of(task, frozenset({REQUEST_REF_KIND}))
        if request_id is not None and request_id in state.extended_request_ids:
            return RejectionCode.extension_already_used
    return None


def failure_cause(error: ActivityError) -> ReviewCause:
    if error.retry_state is RetryState.NON_RETRYABLE_FAILURE:
        return ReviewCause.non_retryable_error
    return ReviewCause.retry_limit_reached


class ReviewLoop:
    """Owns a case's review tasks. Build it in the workflow's run method and start `run()` as a
    background task; the workflow's signal handler feeds `queue` (see `ReviewHandlers`)."""

    def __init__(
        self,
        context: ReviewContext,
        windows: ReviewWindows,
        clock: CaseClock,
        transitions: CaseTransitions,
        queue: deque[CaseworkerActionSignal] | None = None,
    ) -> None:
        self.context = context
        self.state = ReviewState()
        self.queue: deque[CaseworkerActionSignal] = queue if queue is not None else deque()
        self._windows, self._clock, self._transitions = windows, clock, transitions
        self._projected: set[UUID] = set()
        self._wake = False
        self._busy = False

    # --- entry points ------------------------------------------------------------------------

    def enqueue(self, signal: CaseworkerActionSignal) -> None:
        """Signal handler body: queue only, never await (ADR-010-02)."""
        self.queue.append(signal)

    def clock_changed(self) -> None:
        """Call after the case clock jumps (011 advance-clock), so due times are re-evaluated."""
        self._wake = True

    @property
    def idle(self) -> bool:
        """No action is being applied and none is waiting (safe point to end the workflow)."""
        return not self._busy and not self.queue

    def open_tasks(self) -> list[ReviewTaskState]:
        return [t for t in self.state.tasks.values() if t.status is ReviewTaskStatus.open]

    async def open_review(
        self,
        reason: ReviewReason,
        cause: ReviewCause,
        evidence_refs: list[EvidenceRef],
        *,
        coverage_end: date | None = None,
    ) -> UUID:
        """Open a task. Blocking reasons return once it closes; `explanation_review` at once."""
        task_id = await self._open(reason, cause, evidence_refs, coverage_end=coverage_end)
        if self.state.tasks[task_id].blocking:
            await workflow.wait_condition(
                lambda: self.state.tasks[task_id].status is ReviewTaskStatus.closed
            )
        return task_id

    async def run_or_review(
        self,
        step: Callable[[], Awaitable[ResultT]],
        *,
        name: str,
        reason: ReviewReason,
        cause: ReviewCause | None = None,
        evidence_refs: list[EvidenceRef] | None = None,
    ) -> ResultT | None:
        """Run a step; past its retry limit, open a review and wait instead of failing (FR-010-03).

        `step` starts one activity with its preset. `cause` fixes the review cause; by default it
        comes from how the activity failed (`retry_limit_reached` or `non_retryable_error`).
        """
        try:
            return await step()
        except ActivityError as error:
            workflow.logger.warning(
                "review.step_failed", extra={"step": name, "retry_state": str(error.retry_state)}
            )
            await self.open_review(reason, cause or failure_cause(error), evidence_refs or [])
            return None

    async def verify_documents(self) -> VerificationResult | None:
        return await self.run_or_review(
            lambda: workflow.execute_activity(
                VERIFY_DOCUMENTS, self._case_ref(), result_type=VerificationResult, **AGENT_STEP
            ),
            name=VERIFY_DOCUMENTS,
            reason=ReviewReason.verification_review,
        )

    async def evaluate(self) -> EvaluationResult | None:
        return await self.run_or_review(
            lambda: workflow.execute_activity(
                EVALUATE, self._case_ref(), result_type=EvaluationResult, **RULES_STEP
            ),
            name=EVALUATE,
            reason=DETERMINATION_FAILURE_REASON,
            cause=DETERMINATION_FAILURE_CAUSE,
        )

    async def request_information_automatically(self, finding: VerificationFinding) -> UUID | None:
        """At most one automatic request per case (FR-010-04, R1); later ones go to a person."""
        refs = [EvidenceRef(kind=FINDING_REF_KIND, id=finding.finding_id)]
        if self.state.auto_rfi_used:
            await self.open_review(ReviewReason.verification_review, finding.cause, refs)
            return None
        self.state.auto_rfi_used = True
        request_id, cause = await self._draft(RequestOrigin.automatic, [finding.finding_id], None)
        if cause is not None:
            # Drafting failed: a person takes it over, and the case waits for them.
            await self.open_review(ReviewReason.verification_review, cause, refs)
        return request_id

    async def alongside(self, main: Coroutine[object, object, HostT]) -> HostT:
        """Run the host workflow's `main` with the review loop beside it; return `main`'s result.

        The loop never returns on its own, so if it ends first it raised (`ProjectionRejected`).
        That error is raised here, in the workflow's run coroutine, so it fails the workflow task
        and holds the case; an un-awaited loop task would stop silently instead (TW-11).
        """
        loop = asyncio.create_task(self.run())
        work = asyncio.create_task(main)
        try:
            # `workflow.wait`, not `asyncio.wait`: deterministic task ordering on replay.
            await workflow.wait([loop, work], return_when=asyncio.FIRST_COMPLETED)
            if loop.done():
                loop.result()
                raise RuntimeError("review loop stopped without an error")
            return work.result()
        finally:
            loop.cancel()
            work.cancel()

    async def run(self) -> None:
        """Main loop: queued actions first, then due flags, then sleep until the next due time.

        Hosts run it through `alongside`, never as a bare task, so its errors reach the workflow.
        """
        while True:
            self._wake = False
            self._busy = True
            await self._process_queue()
            await self._set_due_flags()
            self._busy = False
            try:
                await workflow.wait_condition(
                    lambda: self._wake or self._has_ready_signal(), timeout=self._next_wait()
                )
            except TimeoutError:
                pass

    # --- opening ---------------------------------------------------------------------------------

    def _case_ref(self) -> CaseRef:
        return CaseRef(household_id=self.context.household_id, case_id=self.context.case_id)

    async def _open(
        self,
        reason: ReviewReason,
        cause: ReviewCause,
        evidence_refs: list[EvidenceRef],
        *,
        coverage_end: date | None,
    ) -> UUID:
        now_real = workflow.now()
        task = ReviewTaskState(
            task_id=workflow.uuid4(),
            household_id=self.context.household_id,
            case_id=self.context.case_id,
            workflow_id=self.context.workflow_id,
            reason=reason,
            cause=cause,
            evidence_refs=evidence_refs,
            blocking=reason in BLOCKING,
            opened_at_case=self._clock.now(),
            opened_at_real=now_real,
            decision_deadline=self.context.decision_deadline,
            coverage_end=coverage_end,
        )
        self.state.tasks[task.task_id] = task
        await self._project(
            "upsert_review_task",
            ReviewTask(**task.model_dump(), created_at=now_real, updated_at=now_real),
        )
        self._projected.add(task.task_id)
        self._wake = True
        workflow.logger.info(
            "review.opened",
            extra={"task_id": str(task.task_id), "reason": reason.value, "cause": cause.value},
        )
        return task.task_id

    async def _project(self, activity: str, arg: BaseModel) -> None:
        """Run an idempotent projection activity until it succeeds (TW-11, US2 "does not stall").

        Past the preset's retries the error is logged and the call tried again after a growing
        wait, so a database outage delays the review loop instead of ending it. A non-retryable
        error (invalid document, schema rejection) raises `ProjectionRejected`, which holds the
        case on a failing workflow task instead of retrying in a loop.
        """
        wait = PROJECTION_RETRY_WAIT
        while True:
            try:
                await workflow.execute_activity(activity, arg, **DEFAULT)
                return
            except ActivityError as error:
                if isinstance(error.cause, CancelledError):
                    raise
                extra = {"activity": activity, "retry_state": str(error.retry_state)}
                if error.retry_state is RetryState.NON_RETRYABLE_FAILURE:
                    workflow.logger.error("review.projection_rejected", extra=extra)
                    raise ProjectionRejected(f"{activity} was rejected") from error
                workflow.logger.error("review.projection_failed", extra=extra)
                await asyncio.sleep(wait.total_seconds())
                wait = min(wait * 2, PROJECTION_RETRY_MAX_WAIT)

    # --- actions ---------------------------------------------------------------------------------

    def _has_ready_signal(self) -> bool:
        return any(
            s.task_id not in self.state.tasks or s.task_id in self._projected for s in self.queue
        )

    async def _process_queue(self) -> None:
        """Queued actions in arrival order; each `action_id` handled once (FR-010-06)."""
        deferred: deque[CaseworkerActionSignal] = deque()
        while self.queue:
            signal = self.queue.popleft()
            if signal.action_id in self.state.seen_action_ids:
                continue
            task = self.state.tasks.get(signal.task_id)
            if task is None:
                self.state.seen_action_ids.add(signal.action_id)
                workflow.logger.warning(
                    "review.action_for_unknown_task", extra={"action_id": str(signal.action_id)}
                )
                continue
            if task.task_id not in self._projected:
                deferred.append(signal)  # wait until the task is in the queue's projection
                continue
            self.state.seen_action_ids.add(signal.action_id)
            code = validate_action(task, signal, self.state)
            if code is None:
                await self._apply(task, signal)
            else:
                await self._record(signal, ActionOutcome.rejected, code)
        self.queue.extend(deferred)

    async def _apply(self, task: ReviewTaskState, signal: CaseworkerActionSignal) -> None:
        action = signal.action
        request_id: UUID | None = None
        if action is ActionType.request_information:
            # A caseworker request: never checks or sets auto_rfi_used (R1, FR-010-04).
            finding_ids = [r.id for r in task.evidence_refs if r.kind == FINDING_REF_KIND]
            request_id, cause = await self._draft(
                RequestOrigin.caseworker, finding_ids, signal.action_id
            )
            if cause is not None:
                # No request was drafted: refuse the action and leave the task open with the
                # caseworker, who can try again or choose another action.
                code = RejectionCode.request_draft_failed
                await self._record(signal, ActionOutcome.rejected, code)
                return
        elif action is ActionType.extend_15_days:
            request_id = ref_of(task, frozenset({REQUEST_REF_KIND}))
            if request_id is not None:
                self.state.extended_request_ids.add(request_id)
        result = ReviewResult(
            task_id=task.task_id,
            action_id=signal.action_id,
            reason=task.reason,
            action=action,
            step=RESULTS[(task.reason, action)],
            reviewer_id=signal.reviewer_id,
            override_action_id=(
                signal.action_id if action is ActionType.override_to_approve else None
            ),
            corrected_value=signal.corrected_value if action is ActionType.correct_value else None,
            dismissed_finding_id=(
                signal.finding_id if action is ActionType.dismiss_finding else None
            ),
            draft_ref=ref_of(task, DRAFT_REF_KINDS),
            revision_id=signal.revision_id if action is ActionType.edit_and_approve else None,
            request_id=request_id,
            coverage_ended_by_caseworker=(
                action is ActionType.deny_missing_information and task.coverage_end is not None
            ),
        )
        await self._transitions.apply(result)
        await self._record(signal, ActionOutcome.applied, None)
        await self._project(
            "close_review_task",
            ReviewTaskClose(
                household_id=task.household_id,
                case_id=task.case_id,
                task_id=task.task_id,
                action_id=signal.action_id,
            ),
        )
        # Closed in state only once projected, so a waiter resumes after the queue shows it.
        self.state.tasks[task.task_id] = self.state.tasks[task.task_id].model_copy(
            update={"status": ReviewTaskStatus.closed, "closed_by_action_id": signal.action_id}
        )
        workflow.logger.info(
            "review.action_applied",
            extra={"task_id": str(task.task_id), "action": action.value, "step": result.step.value},
        )

    async def _record(
        self, signal: CaseworkerActionSignal, outcome: ActionOutcome, code: RejectionCode | None
    ) -> None:
        applied = outcome is ActionOutcome.applied
        record = CaseworkerAction(
            **signal.model_dump(),
            household_id=self.context.household_id,
            case_id=self.context.case_id,
            outcome=outcome,
            rejection_code=code,
            applied_at_case=self._clock.now() if applied else None,
            recorded_at=workflow.now(),
        )
        await self._project("record_caseworker_action", record)
        if not applied:
            workflow.logger.info(
                "review.action_rejected",
                extra={"task_id": str(signal.task_id), "rejection_code": str(code)},
            )

    async def _draft(
        self, origin: RequestOrigin, finding_ids: list[UUID], action_id: UUID | None
    ) -> tuple[UUID | None, ReviewCause | None]:
        """Draft a request for information (002): the request ID, or why drafting failed.

        The caller decides who takes a failed draft over; nothing is applied here.
        """
        data = DraftRequestInput(
            household_id=self.context.household_id,
            case_id=self.context.case_id,
            origin=origin,
            finding_ids=finding_ids,
            action_id=action_id,
        )
        try:
            drafted: DraftRequestResult = await workflow.execute_activity(
                DRAFT_REQUEST, data, result_type=DraftRequestResult, **AGENT_STEP
            )
        except ActivityError as error:
            workflow.logger.warning(
                "review.draft_failed", extra={"retry_state": str(error.retry_state)}
            )
            return None, failure_cause(error)
        return drafted.request_id, None

    # --- reminder and escalation -----------------------------------------------------------------

    def _pending_flags(self, task: ReviewTaskState) -> list[tuple[ReviewFlag, datetime]]:
        pending: list[tuple[ReviewFlag, datetime]] = []
        if ReviewFlag.reminder not in task.flags:
            pending.append((ReviewFlag.reminder, reminder_due(task.opened_at_case, self._windows)))
        if ReviewFlag.escalated not in task.flags:
            due = escalation_due(task.opened_at_case, task.coverage_end, self._windows)
            pending.append((ReviewFlag.escalated, due))
        return pending

    async def _set_due_flags(self) -> None:
        """Only tasks still open get a flag: an action queued at the same instant wins."""
        now = self._clock.now()
        for task in self.open_tasks():
            for flag, due in self._pending_flags(task):
                if due > now + DUE_TOLERANCE:
                    continue
                await self._project(
                    "set_review_flag",
                    ReviewFlagUpdate(
                        household_id=task.household_id,
                        case_id=task.case_id,
                        task_id=task.task_id,
                        flag=flag,
                        at_case=now,
                    ),
                )
                # In state once projected, like a close.
                current = self.state.tasks[task.task_id]
                stamp = "reminder_at" if flag is ReviewFlag.reminder else "escalated_at"
                self.state.tasks[task.task_id] = current.model_copy(
                    update={"flags": [*current.flags, flag], stamp: now}
                )
                workflow.logger.info(
                    "review.flagged", extra={"task_id": str(task.task_id), "flag": flag.value}
                )

    def _next_wait(self) -> timedelta | None:
        dues = [due for task in self.open_tasks() for _, due in self._pending_flags(task)]
        if not dues:
            return None
        return max(self._clock.real_duration_until(min(dues)), MIN_WAIT)


class ReviewHandlers:
    """Mixin for the workflow class: the `caseworker_action` signal and `open_review_tasks` query
    (contracts/workflow-review.md). The class sets `review_queue` in `__init__` and `review` in
    its run method; signals that arrive before `review` exists wait in the queue."""

    review_queue: deque[CaseworkerActionSignal]
    review: ReviewLoop | None = None

    @workflow.signal(name="caseworker_action")
    def caseworker_action(self, signal: CaseworkerActionSignal) -> None:
        self.review_queue.append(signal)

    @workflow.query(name="open_review_tasks")
    def open_review_tasks(self) -> list[ReviewTaskState]:
        return self.review.open_tasks() if self.review is not None else []
