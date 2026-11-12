"""A test workflow that runs the pay stub branch the way 011's `case.py` hook will (ADR-002-06):
`verify_documents` -> `review_cause` -> review task -> caseworker action ->
`apply_verification_action`.

`case.py` is not edited by 002; this proves the pieces fit with stubbed activities (TW-15). While a
person is needed the case is not locked, so an exhausted activity never leaves it stuck (TW-10).
"""

from __future__ import annotations

from collections import deque
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from apps.worker.policies import NON_RETRYABLE_ERRORS
    from apps.worker.workflows.verification_branch import next_step, review_cause
    from packages.domain.review import (
        ActionOutcome,
        CaseworkerAction,
        CaseworkerActionSignal,
        ReviewCause,
        ReviewReason,
        ReviewTask,
    )
    from packages.domain.verification import VerificationRequest, VerificationResult

OPTIONS: dict[str, Any] = {
    "start_to_close_timeout": timedelta(seconds=30),
    "retry_policy": RetryPolicy(
        initial_interval=timedelta(milliseconds=10),
        maximum_attempts=3,
        non_retryable_error_types=NON_RETRYABLE_ERRORS,
    ),
}


class BranchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: str
    cause: ReviewCause | None
    locked: bool


@workflow.defn(name="VerificationBranchWorkflow")
class VerificationBranchWorkflow:
    def __init__(self) -> None:
        self._actions: deque[CaseworkerActionSignal] = deque()
        self._locked = False
        self._open_cause: ReviewCause | None = None

    @workflow.signal(name="caseworker_action")
    def caseworker_action(self, action: CaseworkerActionSignal) -> None:
        self._actions.append(action)

    @workflow.query
    def locked(self) -> bool:
        return self._locked

    @workflow.query
    def open_cause(self) -> str | None:
        return self._open_cause.value if self._open_cause else None

    @workflow.run
    async def run(self, request: VerificationRequest) -> BranchResult:
        self._locked = True
        try:
            result: VerificationResult = await workflow.execute_activity(
                "verify_documents", request, result_type=VerificationResult, **OPTIONS
            )
        except ActivityError:
            # Retries are exhausted: a person looks at it, and the case is not left locked.
            await self._open_task(request, ReviewCause.verification_unavailable)
            self._locked = False
            return BranchResult(
                outcome="verification_unavailable",
                cause=ReviewCause.verification_unavailable,
                locked=self._locked,
            )
        cause = review_cause(result)
        first_cause = cause
        while cause is not None:
            await self._open_task(request, cause)
            result = await self._until_resolved(request, result)
            cause = review_cause(result)
            self._open_cause = None
        self._locked = False
        step = next_step(result, request_already_sent=False)
        return BranchResult(outcome=step.value, cause=first_cause, locked=self._locked)

    async def _until_resolved(
        self, request: VerificationRequest, result: VerificationResult
    ) -> VerificationResult:
        """Wait for caseworker actions until one is applied. A failed apply leaves the task open."""
        while True:
            self._locked = False  # waiting for a person never holds the application lock
            await workflow.wait_condition(lambda: bool(self._actions))
            signal = self._actions.popleft()
            self._locked = True
            stored = CaseworkerAction.model_validate(
                {
                    **signal.model_dump(),
                    "household_id": request.household_id,
                    "case_id": request.case_id,
                    "outcome": ActionOutcome.applied,
                    "recorded_at": workflow.now(),
                }
            )
            try:
                return await workflow.execute_activity(
                    "apply_verification_action",
                    stored,
                    result_type=VerificationResult,
                    **OPTIONS,
                )
            except ActivityError:
                continue

    async def _open_task(self, request: VerificationRequest, cause: ReviewCause) -> None:
        now = workflow.now()
        self._open_cause = cause
        assert request.as_of_date is not None  # noqa: S101 - the test always sets it
        task = ReviewTask(
            task_id=workflow.uuid4(),
            household_id=request.household_id,
            case_id=request.case_id,
            workflow_id=workflow.info().workflow_id,
            reason=ReviewReason.verification_review,
            cause=cause,
            blocking=True,
            opened_at_case=now,
            opened_at_real=now,
            decision_deadline=request.as_of_date + timedelta(days=30),
            created_at=now,
            updated_at=now,
        )
        await workflow.execute_activity("upsert_review_task", task, **OPTIONS)
