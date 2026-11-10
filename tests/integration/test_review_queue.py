"""The review queue end to end inside the backend: case workflow -> worker activities -> MongoDB
(FR-010-01, FR-010-02, FR-010-03, FR-010-06; TC-42 to TC-45, quickstart.md steps 3 and 4).

Real pieces: the Temporal test server (time-skipping, so demo minutes pass instantly), the review
activities and repositories from `apps/worker/main.py`, and the local MongoDB with migration 0002
applied by `reset_stores`. Stubbed: the 002/003 agent activities (no model call, FR-000-20). Data:
the seeded synthetic household (BA-11) and generated case IDs.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from uuid import UUID

import pytest
from temporalio import activity
from temporalio.client import WorkflowHandle
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from apps.worker.main import review_activities
from apps.worker.repositories.review_tasks import ReviewTaskRepository
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
    VerificationResult,
)
from apps.worker.workflows.review_timers import ReviewWindowSettings
from db.seeds.ids import HOUSEHOLD_A
from packages.domain.review import (
    ActionOutcome,
    ActionType,
    ReviewCause,
    ReviewFlag,
    ReviewReason,
    ReviewTask,
    ReviewTaskState,
    ReviewTaskStatus,
)
from packages.platform.clock import SystemClock
from packages.platform.mongo import Client, make_client
from packages.platform.settings import Settings
from tests.apps.worker.review_support import action_signal

pytestmark = pytest.mark.integration

TASK_QUEUE = "test-review-queue"
DEADLINE = date(2026, 4, 16)
# One case day per real minute in the demo profile (FR-011-04).
DEMO_TIME_SCALE = 1440.0
POLL_S, POLL_LIMIT = 0.05, 600  # up to 30 s real time


@dataclass
class AgentStubs:
    """Stand-ins for the 002/003 agent activities. Nothing here calls a model."""

    evaluation: EligibilityOutcome = EligibilityOutcome.ineligible
    verify_error: ApplicationError | None = None
    verify_attempts: int = 0
    drafts: list[DraftRequestInput] = field(default_factory=list)

    def activities(self) -> list[Callable[..., Any]]:
        @activity.defn(name="verify_documents")
        async def verify_documents(case: CaseRef) -> VerificationResult:
            self.verify_attempts += 1
            if self.verify_error is not None:
                raise self.verify_error
            return VerificationResult()

        @activity.defn(name="evaluate")
        async def evaluate(case: CaseRef) -> EvaluationResult:
            return EvaluationResult(determination_id=uuid.uuid4(), outcome=self.evaluation)

        @activity.defn(name="draft_request")
        async def draft_request(data: DraftRequestInput) -> DraftRequestResult:
            self.drafts.append(data)
            return DraftRequestResult(request_id=uuid.uuid4())

        return [verify_documents, evaluate, draft_request]


@pytest.fixture
async def mongo(app_url: str) -> AsyncIterator[Client]:
    """The runtime user the worker connects as."""
    client = make_client(app_url)
    try:
        yield client
    finally:
        await client.close()


@asynccontextmanager
async def review_stack(
    settings: Settings, mongo: Client, stubs: AgentStubs
) -> AsyncIterator[WorkflowEnvironment]:
    review = review_activities(settings, mongo, SystemClock())
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        async with Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[ReviewHarnessWorkflow],
            activities=[
                review.upsert_review_task,
                review.set_review_flag,
                review.close_review_task,
                review.record_caseworker_action,
                *stubs.activities(),
            ],
        ):
            yield env


def case_input(
    settings: Settings, scenario: HarnessScenario, *, wait_for_finish: bool = False
) -> ReviewHarnessInput:
    """What the case starter passes in: windows from settings, demo time scale from the profile."""
    return ReviewHarnessInput(
        household_id=HOUSEHOLD_A,
        case_id=uuid.uuid4(),
        decision_deadline=DEADLINE,
        scenario=scenario,
        subject_id=uuid.uuid4(),
        windows=ReviewWindowSettings.from_settings(settings),
        time_scale=DEMO_TIME_SCALE if settings.DEMO_PROFILE else 1.0,
        wait_for_finish=wait_for_finish,
    )


async def start(env: WorkflowEnvironment, data: ReviewHarnessInput) -> WorkflowHandle[Any, Any]:
    return await env.client.start_workflow(
        ReviewHarnessWorkflow.run, data, id=f"case-{data.case_id}", task_queue=TASK_QUEUE
    )


async def open_task(handle: WorkflowHandle[Any, Any]) -> ReviewTaskState:
    for _ in range(POLL_LIMIT):
        tasks = await handle.query(ReviewHarnessWorkflow.open_review_tasks)
        if tasks:
            return tasks[0]
        await asyncio.sleep(POLL_S)
    raise AssertionError("no review task opened")


async def stored(
    settings: Settings, mongo: Client, task: ReviewTaskState, until: Callable[[ReviewTask], bool]
) -> ReviewTask:
    """Poll `review_tasks` (the queue's read model, ADR-010-01) until `until` holds."""
    repo = ReviewTaskRepository(mongo[settings.MONGO_DB], SystemClock())
    found: ReviewTask | None = None
    for _ in range(POLL_LIMIT):
        found = await repo.get(task.household_id, task.case_id, task.task_id)
        if found is not None and until(found):
            return found
        await asyncio.sleep(POLL_S)
    raise AssertionError(f"review_tasks never reached the expected state: {found}")


async def recorded(settings: Settings, mongo: Client, action_id: UUID) -> list[dict[str, Any]]:
    return await (
        mongo[settings.MONGO_DB]["caseworker_actions"].find({"action_id": action_id}).to_list()
    )


async def test_FR_010_01_ineligible_case_opens_adverse_task_in_review_tasks(
    integration_settings: Settings, mongo: Client
) -> None:
    stubs = AgentStubs(evaluation=EligibilityOutcome.ineligible)

    async with review_stack(integration_settings, mongo, stubs) as env:
        handle = await start(env, case_input(integration_settings, HarnessScenario.determine))
        task = await open_task(handle)
        row = await stored(integration_settings, mongo, task, lambda _: True)

    assert (row.reason, row.cause) == (ReviewReason.adverse_determination, ReviewCause.ineligible)
    assert row.status is ReviewTaskStatus.open
    assert row.blocking is True
    assert (row.household_id, row.case_id) == (HOUSEHOLD_A, task.case_id)
    assert row.workflow_id == f"case-{task.case_id}"
    assert [ref.kind for ref in row.evidence_refs] == [task.evidence_refs[0].kind]


async def test_FR_010_06_signal_sent_twice_one_applied_record(
    integration_settings: Settings, mongo: Client
) -> None:
    stubs = AgentStubs(evaluation=EligibilityOutcome.ineligible)
    data = case_input(integration_settings, HarnessScenario.determine, wait_for_finish=True)

    async with review_stack(integration_settings, mongo, stubs) as env:
        handle = await start(env, data)
        task = await open_task(handle)
        signal = action_signal(task.task_id, ActionType.approve_denial)
        await handle.signal(ReviewHarnessWorkflow.caseworker_action, signal)
        await handle.signal(ReviewHarnessWorkflow.caseworker_action, signal)
        row = await stored(
            integration_settings, mongo, task, lambda t: t.status is ReviewTaskStatus.closed
        )
        await handle.signal(ReviewHarnessWorkflow.finish)
        result = await handle.result()
        records = await recorded(integration_settings, mongo, signal.action_id)

    assert [r["outcome"] for r in records] == [ActionOutcome.applied.value]
    assert row.closed_by_action_id == signal.action_id
    assert [r.action_id for r in result.results] == [signal.action_id]


async def test_FR_010_02_demo_profile_reminder_and_escalation_flags_in_projection(
    integration_settings: Settings, mongo: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEMO_PROFILE", "true")
    settings = integration_settings.model_copy(update={"DEMO_PROFILE": True})
    stubs = AgentStubs(evaluation=EligibilityOutcome.ineligible)

    async with review_stack(settings, mongo, stubs) as env:
        handle = await start(env, case_input(settings, HarnessScenario.determine))
        task = await open_task(handle)
        await env.sleep(timedelta(minutes=2, seconds=5))
        after_2 = await stored(settings, mongo, task, lambda t: ReviewFlag.reminder in t.flags)
        await env.sleep(timedelta(minutes=2))
        after_4 = await stored(settings, mongo, task, lambda t: ReviewFlag.escalated in t.flags)

    assert after_2.flags == [ReviewFlag.reminder]
    assert after_4.flags == [ReviewFlag.reminder, ReviewFlag.escalated]
    assert after_4.reminder_at is not None and after_4.escalated_at is not None
    assert after_4.reminder_at - after_4.opened_at_case >= timedelta(days=2)
    assert after_4.escalated_at - after_4.opened_at_case >= timedelta(days=4)
    assert after_4.status is ReviewTaskStatus.open


async def test_FR_010_03_failing_verification_projects_review_task(
    integration_settings: Settings, mongo: Client
) -> None:
    stubs = AgentStubs(
        verify_error=ApplicationError("verification service unavailable", type="Unavailable")
    )

    async with review_stack(integration_settings, mongo, stubs) as env:
        handle = await start(env, case_input(integration_settings, HarnessScenario.verify))
        task = await open_task(handle)
        row = await stored(integration_settings, mongo, task, lambda _: True)

    assert stubs.verify_attempts == 3
    assert (row.reason, row.cause) == (
        ReviewReason.verification_review,
        ReviewCause.retry_limit_reached,
    )
    assert row.status is ReviewTaskStatus.open
