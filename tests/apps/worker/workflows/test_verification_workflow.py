"""The pay stub branch in a test workflow, activities stubbed (TW-15; FR-002-16, -19, -25)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from temporalio import activity
from temporalio.client import WorkflowHandle
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from packages.domain.auth import Role
from packages.domain.review import (
    ActionType,
    CaseworkerAction,
    CaseworkerActionSignal,
    ReviewReason,
    ReviewTask,
)
from packages.domain.verification import (
    Finding,
    FindingCause,
    VerificationRequest,
    VerificationResult,
)
from tests.apps.worker.workflows.verification_workflow import (
    BranchResult,
    VerificationBranchWorkflow,
)

TASK_QUEUE = "test-verification"
CASE_ID, HOUSEHOLD_ID = uuid4(), uuid4()
FINDING_ID = uuid4()
REQUEST = VerificationRequest(
    case_id=CASE_ID, household_id=HOUSEHOLD_ID, as_of_date=datetime(2026, 3, 17, tzinfo=UTC).date()
)
OPEN = VerificationResult(
    complete=True,
    needs_review=True,
    case_id=CASE_ID,
    findings=[Finding(finding_id=FINDING_ID, cause=FindingCause.income_mismatch)],
)
CLEAN = VerificationResult(complete=True, needs_review=False, case_id=CASE_ID)


@dataclass
class Stubs:
    verify_result: VerificationResult = field(default_factory=lambda: OPEN)
    apply_result: VerificationResult = field(default_factory=lambda: CLEAN)
    verify_error: ApplicationError | None = None
    apply_error: ApplicationError | None = None
    verify_attempts: int = 0
    apply_attempts: int = 0
    tasks: list[ReviewTask] = field(default_factory=list)
    applied: list[CaseworkerAction] = field(default_factory=list)

    def activities(self) -> list[Callable[..., Any]]:
        @activity.defn(name="verify_documents")
        async def verify_documents(request: VerificationRequest) -> VerificationResult:
            self.verify_attempts += 1
            if self.verify_error is not None:
                raise self.verify_error
            return self.verify_result

        @activity.defn(name="apply_verification_action")
        async def apply_verification_action(action: CaseworkerAction) -> VerificationResult:
            self.apply_attempts += 1
            if self.apply_error is not None:
                raise self.apply_error
            self.applied.append(action)
            return self.apply_result

        @activity.defn(name="upsert_review_task")
        async def upsert_review_task(task: ReviewTask) -> None:
            self.tasks.append(task)

        return [verify_documents, apply_verification_action, upsert_review_task]


@asynccontextmanager
async def _env(stubs: Stubs) -> AsyncIterator[WorkflowEnvironment]:
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        async with Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[VerificationBranchWorkflow],
            activities=stubs.activities(),
        ):
            yield env


async def _start(env: WorkflowEnvironment) -> WorkflowHandle[Any, Any]:
    return await env.client.start_workflow(
        VerificationBranchWorkflow.run, REQUEST, id=f"case-{CASE_ID}", task_queue=TASK_QUEUE
    )


async def _until(condition: Callable[[], bool], what: str) -> None:
    for _ in range(600):  # real time: a query or a poll never moves the test clock
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def _action(kind: ActionType) -> CaseworkerActionSignal:
    return CaseworkerActionSignal(
        action_id=uuid4(),
        task_id=uuid4(),
        action=kind,
        reviewer_id=uuid4(),
        reviewer_role=Role.caseworker,
        reason="Checked against the employer letter",
        finding_id=FINDING_ID,
        submitted_at=datetime(2026, 3, 18, tzinfo=UTC),
    )


async def test_FR_002_16_open_finding_routes_to_verification_review() -> None:
    stubs = Stubs()
    async with _env(stubs) as env:
        handle = await _start(env)
        await _until(lambda: bool(stubs.tasks), "the verification review task")

        (task,) = stubs.tasks
        assert task.reason is ReviewReason.verification_review
        assert task.cause.value == "income_mismatch" and task.blocking
        assert await handle.query(VerificationBranchWorkflow.open_cause) == "income_mismatch"

        await handle.signal("caseworker_action", _action(ActionType.dismiss_finding))
        result: BranchResult = await handle.result()

    assert result.outcome == "determine" and result.locked is False
    assert [a.finding_id for a in stubs.applied] == [FINDING_ID]  # re-run through the activity


async def test_FR_002_25_verify_activity_retries_exhausted_case_not_left_locked() -> None:
    stubs = Stubs(verify_error=ApplicationError("store unavailable", type="StoreUnavailable"))
    async with _env(stubs) as env:
        handle = await _start(env)
        result: BranchResult = await handle.result()  # ends; it is not left running

    assert stubs.verify_attempts == 3  # the policy's budget, then it gives up
    assert result.outcome == "verification_unavailable" and result.locked is False
    (task,) = stubs.tasks
    assert task.cause.value == "verification_unavailable"  # a person is told


async def test_FR_002_19_apply_action_retries_exhausted_case_not_left_locked() -> None:
    stubs = Stubs(apply_error=ApplicationError("store unavailable", type="StoreUnavailable"))
    async with _env(stubs) as env:
        handle = await _start(env)
        await _until(lambda: bool(stubs.tasks), "the verification review task")
        await handle.signal("caseworker_action", _action(ActionType.correct_value))
        await _until(lambda: stubs.apply_attempts == 3, "the retries to be used up")

        # The task stays open for the caseworker and the case is not locked.
        async def settled() -> bool:
            return (
                await handle.query(VerificationBranchWorkflow.locked) is False
                and await handle.query(VerificationBranchWorkflow.open_cause) == "income_mismatch"
            )

        for _ in range(100):
            if await settled():
                break
            await asyncio.sleep(0.05)
        assert await settled()
        assert len(stubs.tasks) == 1  # no duplicate task

        # The caseworker tries again once the store is back and the branch finishes.
        stubs.apply_error = None
        await handle.signal("caseworker_action", _action(ActionType.dismiss_finding))
        result: BranchResult = await handle.result()

    assert result.outcome == "determine" and result.locked is False
