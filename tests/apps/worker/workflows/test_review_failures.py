"""Stuck work goes to a person and the case never stalls (FR-010-03, FR-010-04, ADR-010-04,
ADR-010-05 option A)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Sequence

import pytest
from temporalio.api.history.v1 import HistoryEvent
from temporalio.client import WorkflowExecutionStatus
from temporalio.exceptions import ApplicationError

from apps.worker.workflows.review import DETERMINATION_FAILURE_REASON
from apps.worker.workflows.review_harness import HarnessScenario, ReviewHarnessWorkflow
from apps.worker.workflows.review_ports import CaseStep, RequestOrigin, VerificationFinding
from packages.domain.review import (
    ActionOutcome,
    ActionType,
    RejectionCode,
    ReviewCause,
    ReviewReason,
)
from tests.apps.worker.review_support import action_signal
from tests.apps.worker.workflows.review_env import (
    Stubs,
    harness_input,
    review_env,
    start,
    wait_for_open_tasks,
)


def retryable() -> ApplicationError:
    return ApplicationError("verification service unavailable", type="ServiceUnavailable")


async def test_FR_010_03_verification_fails_3_times_opens_verification_review() -> None:
    stubs = Stubs(verify_error=retryable())

    async with review_env(stubs) as env:
        handle = await start(env, harness_input(HarnessScenario.verify))
        [task] = await wait_for_open_tasks(handle)
        status = (await handle.describe()).status

    assert stubs.verify_attempts == 3
    assert (task.reason, task.cause) == (
        ReviewReason.verification_review,
        ReviewCause.retry_limit_reached,
    )
    assert status is WorkflowExecutionStatus.RUNNING


async def test_FR_010_03_determination_fails_3_times_opens_review_and_does_not_stall() -> None:
    stubs = Stubs(evaluate_error=retryable())

    async with review_env(stubs) as env:
        handle = await start(env, harness_input(HarnessScenario.determine))
        [task] = await wait_for_open_tasks(handle)
        status = (await handle.describe()).status

    assert stubs.evaluate_attempts == 3
    assert DETERMINATION_FAILURE_REASON is ReviewReason.cannot_determine
    assert (task.reason, task.cause) == (
        ReviewReason.cannot_determine,
        ReviewCause.determination_failed,
    )
    assert status is WorkflowExecutionStatus.RUNNING


async def test_FR_010_03_non_retryable_error_opens_review_after_1_attempt() -> None:
    stubs = Stubs(verify_error=ApplicationError("bad document input", type="ValidationError"))

    async with review_env(stubs) as env:
        handle = await start(env, harness_input(HarnessScenario.verify))
        [task] = await wait_for_open_tasks(handle)
        status = (await handle.describe()).status

    assert stubs.verify_attempts == 1
    assert (task.reason, task.cause) == (
        ReviewReason.verification_review,
        ReviewCause.non_retryable_error,
    )
    assert status is WorkflowExecutionStatus.RUNNING


async def test_FR_010_04_second_automatic_request_opens_verification_review() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    stubs = Stubs(
        findings=[
            VerificationFinding(
                finding_id=first, cause=ReviewCause.unreadable_document, missing_information=True
            ),
            VerificationFinding(
                finding_id=second, cause=ReviewCause.unreadable_document, missing_information=True
            ),
        ]
    )

    async with review_env(stubs) as env:
        handle = await start(env, harness_input(HarnessScenario.verify))
        [task] = await wait_for_open_tasks(handle)

    assert [(d.origin, d.finding_ids) for d in stubs.drafts] == [(RequestOrigin.automatic, [first])]
    assert task.reason is ReviewReason.verification_review
    assert task.cause is ReviewCause.unreadable_document
    assert [ref.id for ref in task.evidence_refs] == [second]


async def _wait_until(check: Callable[[], object], message: str) -> None:
    for _ in range(600):
        if check():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(message)


@pytest.mark.parametrize("failing", ["close", "record"])
async def test_FR_010_03_projection_fails_past_retries_is_retried_and_case_does_not_stall(
    failing: str,
) -> None:
    # One more failure than DEFAULT's 5 attempts: the first call exhausts its retries.
    stubs = Stubs(
        close_failures=6 if failing == "close" else 0,
        record_failures=6 if failing == "record" else 0,
    )

    async with review_env(stubs) as env:
        handle = await start(env, harness_input(HarnessScenario.determine))
        [task] = await wait_for_open_tasks(handle)
        signal = action_signal(task.task_id, ActionType.approve_denial)
        await handle.signal(ReviewHarnessWorkflow.caseworker_action, signal)
        result = await handle.result()

    attempts = stubs.close_attempts if failing == "close" else stubs.record_attempts
    assert attempts == 7
    assert [r.action_id for r in result.results] == [signal.action_id]
    assert [r.action_id for r in stubs.records] == [signal.action_id]
    assert [(c.task_id, c.action_id) for c in stubs.closes] == [(task.task_id, signal.action_id)]


async def test_FR_010_03_projection_rejected_holds_case_without_retry_loop() -> None:
    # A non-retryable projection error can't be fixed by retrying: the case is held on a failing
    # workflow task (no new history) instead of retrying the write every few minutes.
    stubs = Stubs(close_rejected=True)

    async with review_env(stubs) as env:
        handle = await start(env, harness_input(HarnessScenario.determine))
        [task] = await wait_for_open_tasks(handle)
        signal = action_signal(task.task_id, ActionType.approve_denial)
        await handle.signal(ReviewHarnessWorkflow.caseworker_action, signal)
        # Time can't be skipped while a workflow task keeps failing, so wait in real time.
        failures: list[str] = []
        for _ in range(200):
            history = await handle.fetch_history()
            failures = _task_failures(history.events)
            if failures:
                break
            await asyncio.sleep(0.05)
        events_before = len(history.events)
        await asyncio.sleep(2)
        events_after = len((await handle.fetch_history()).events)
        description = await handle.describe()

    # The error reached the workflow task (a silently stopped loop would record none) ...
    assert failures and all("close_review_task was rejected" in m for m in failures)
    # ... and the case is held: one write attempt, still running, no history growth.
    assert stubs.close_attempts == 1
    assert stubs.closes == []
    assert description.status is WorkflowExecutionStatus.RUNNING
    assert events_after == events_before


def _task_failures(events: Sequence[HistoryEvent]) -> list[str]:
    return [
        e.workflow_task_failed_event_attributes.failure.message
        for e in events
        if e.HasField("workflow_task_failed_event_attributes")
    ]


async def test_FR_010_04_caseworker_request_draft_fails_rejected_and_task_stays_open() -> None:
    finding_id = uuid.uuid4()
    stubs = Stubs(
        findings=[VerificationFinding(finding_id=finding_id, cause=ReviewCause.low_confidence)],
        draft_error=retryable(),
    )

    async with review_env(stubs) as env:
        handle = await start(env, harness_input(HarnessScenario.verify))
        [task] = await wait_for_open_tasks(handle)
        request = action_signal(task.task_id, ActionType.request_information)
        await handle.signal(ReviewHarnessWorkflow.caseworker_action, request)
        await _wait_until(lambda: stubs.records, "the failed request was never recorded")
        still_open = await wait_for_open_tasks(handle)
        steps = await handle.query(ReviewHarnessWorkflow.progress, result_type=list[str])
        accept = action_signal(task.task_id, ActionType.accept_findings)
        await handle.signal(ReviewHarnessWorkflow.caseworker_action, accept)
        result = await handle.result()

    [rejected, applied] = stubs.records
    assert (rejected.action_id, rejected.outcome, rejected.rejection_code) == (
        request.action_id,
        ActionOutcome.rejected,
        RejectionCode.request_draft_failed,
    )
    assert [t.task_id for t in still_open] == [task.task_id]
    assert "verify:continued" not in steps
    assert applied.action_id == accept.action_id
    assert [(r.action, r.step) for r in result.results] == [
        (ActionType.accept_findings, CaseStep.determining)
    ]
    assert [c.action_id for c in stubs.closes] == [accept.action_id]


async def test_FR_010_04_automatic_request_draft_fails_opens_review_and_case_waits() -> None:
    finding_id = uuid.uuid4()
    stubs = Stubs(
        findings=[
            VerificationFinding(
                finding_id=finding_id,
                cause=ReviewCause.unreadable_document,
                missing_information=True,
            )
        ],
        draft_error=retryable(),
    )

    async with review_env(stubs) as env:
        handle = await start(env, harness_input(HarnessScenario.verify))
        [task] = await wait_for_open_tasks(handle)
        steps = await handle.query(ReviewHarnessWorkflow.progress, result_type=list[str])
        accept = action_signal(task.task_id, ActionType.accept_findings)
        await handle.signal(ReviewHarnessWorkflow.caseworker_action, accept)
        result = await handle.result()

    assert [d.origin for d in stubs.drafts] == [RequestOrigin.automatic] * 3  # AGENT_STEP tries
    assert (task.reason, task.cause) == (
        ReviewReason.verification_review,
        ReviewCause.retry_limit_reached,
    )
    assert task.blocking is True
    assert [ref.id for ref in task.evidence_refs] == [finding_id]
    assert "verify:continued" not in steps
    assert "verify:continued" in result.progress
    assert [r.action_id for r in result.results] == [accept.action_id]
