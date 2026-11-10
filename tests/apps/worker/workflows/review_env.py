"""Time-skipping environment and stub activities for the review workflow tests (TW-13, TW-15).

The stubs record every call so tests can assert what the workflow asked for. Nothing here touches
MongoDB, an agent or the Anthropic API.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import UUID

from temporalio import activity
from temporalio.client import WorkflowHandle
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from apps.worker.review_payloads import ReviewFlagUpdate, ReviewTaskClose
from apps.worker.workflows.review_harness import (
    HarnessScenario,
    ReviewHarnessInput,
    ReviewHarnessWorkflow,
)
from apps.worker.workflows.review_ports import (
    CaseRef,
    DraftRequestInput,
    DraftRequestResult,
    EligibilityOutcome,
    EvaluationResult,
    VerificationFinding,
    VerificationResult,
)
from apps.worker.workflows.review_timers import ReviewWindowSettings
from packages.domain.review import CaseworkerAction, ReviewTask, ReviewTaskState

TASK_QUEUE = "test-review"
DEADLINE = date(2026, 4, 16)


@dataclass
class Stubs:
    """Stub activities. Set `*_error` to an ApplicationError to make that activity always fail."""

    evaluation: EligibilityOutcome = EligibilityOutcome.ineligible
    findings: list[VerificationFinding] = field(default_factory=list)
    verify_error: ApplicationError | None = None
    evaluate_error: ApplicationError | None = None
    draft_error: ApplicationError | None = None
    # Fail the first N attempts of a projection activity (DEFAULT allows 5 per call).
    close_failures: int = 0
    record_failures: int = 0
    # Make every close attempt fail with a non-retryable error (an invalid document).
    close_rejected: bool = False
    determination_id: UUID = field(default_factory=uuid.uuid4)
    upserts: list[ReviewTask] = field(default_factory=list)
    flags: list[ReviewFlagUpdate] = field(default_factory=list)
    closes: list[ReviewTaskClose] = field(default_factory=list)
    records: list[CaseworkerAction] = field(default_factory=list)
    drafts: list[DraftRequestInput] = field(default_factory=list)
    drafted_ids: list[UUID] = field(default_factory=list)
    close_attempts: int = 0
    record_attempts: int = 0
    verify_attempts: int = 0
    evaluate_attempts: int = 0

    def activities(self) -> list[Callable[..., Any]]:
        @activity.defn(name="upsert_review_task")
        async def upsert_review_task(task: ReviewTask) -> None:
            self.upserts.append(task)

        @activity.defn(name="set_review_flag")
        async def set_review_flag(update: ReviewFlagUpdate) -> bool:
            self.flags.append(update)
            return True

        @activity.defn(name="close_review_task")
        async def close_review_task(close: ReviewTaskClose) -> bool:
            self.close_attempts += 1
            if self.close_rejected:
                raise ApplicationError(
                    "document failed validation", type="ValidationError", non_retryable=True
                )
            if self.close_attempts <= self.close_failures:
                raise ApplicationError("review store unavailable", type="StoreUnavailable")
            self.closes.append(close)
            return True

        @activity.defn(name="record_caseworker_action")
        async def record_caseworker_action(action: CaseworkerAction) -> None:
            self.record_attempts += 1
            if self.record_attempts <= self.record_failures:
                raise ApplicationError("review store unavailable", type="StoreUnavailable")
            self.records.append(action)

        @activity.defn(name="draft_request")
        async def draft_request(data: DraftRequestInput) -> DraftRequestResult:
            self.drafts.append(data)
            if self.draft_error is not None:
                raise self.draft_error
            request_id = uuid.uuid4()
            self.drafted_ids.append(request_id)
            return DraftRequestResult(request_id=request_id)

        @activity.defn(name="verify_documents")
        async def verify_documents(case: CaseRef) -> VerificationResult:
            self.verify_attempts += 1
            if self.verify_error is not None:
                raise self.verify_error
            return VerificationResult(findings=self.findings)

        @activity.defn(name="evaluate")
        async def evaluate(case: CaseRef) -> EvaluationResult:
            self.evaluate_attempts += 1
            if self.evaluate_error is not None:
                raise self.evaluate_error
            return EvaluationResult(determination_id=self.determination_id, outcome=self.evaluation)

        return [
            upsert_review_task,
            set_review_flag,
            close_review_task,
            record_caseworker_action,
            draft_request,
            verify_documents,
            evaluate,
        ]


def harness_input(
    scenario: HarnessScenario,
    *,
    coverage_end: date | None = None,
    subject_id: UUID | None = None,
    windows: ReviewWindowSettings | None = None,
) -> ReviewHarnessInput:
    return ReviewHarnessInput(
        household_id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        decision_deadline=DEADLINE,
        scenario=scenario,
        subject_id=subject_id or uuid.uuid4(),
        coverage_end=coverage_end,
        windows=windows or ReviewWindowSettings(),
    )


@asynccontextmanager
async def review_env(stubs: Stubs) -> AsyncIterator[WorkflowEnvironment]:
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        async with Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[ReviewHarnessWorkflow],
            activities=stubs.activities(),
        ):
            yield env


async def start(env: WorkflowEnvironment, data: ReviewHarnessInput) -> WorkflowHandle[Any, Any]:
    return await env.client.start_workflow(
        ReviewHarnessWorkflow.run,
        data,
        id=f"case-{data.case_id}",
        task_queue=TASK_QUEUE,
    )


async def wait_for_open_tasks(
    handle: WorkflowHandle[Any, Any], count: int = 1
) -> list[ReviewTaskState]:
    """Poll the query (real time only; a query never moves the test clock).

    Up to 30 s, so activity retries with real backoff (a few seconds) can finish first.
    """
    for _ in range(600):
        tasks: list[ReviewTaskState] = await handle.query(
            ReviewHarnessWorkflow.open_review_tasks, result_type=list[ReviewTaskState]
        )
        if len(tasks) >= count:
            return tasks
        await asyncio.sleep(0.05)
    raise AssertionError(f"expected {count} open review task(s)")
