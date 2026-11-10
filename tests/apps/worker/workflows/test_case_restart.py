"""Crash tests (TW-16; US2, SC-001-01): a new worker resumes the case with no repeated effect."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio.worker import Worker

from apps.worker.workflows.case import CaseWorkflow
from packages.domain.case import CaseChange, CaseOutcome, CaseState, CaseworkerActionType
from tests.apps.worker.workflows.case_harness import (
    ELIGIBLE,
    INELIGIBLE,
    TASK_QUEUE,
    Stubs,
    action,
    environment,
    signal,
    start,
    wait_for_state,
    worker,
)

# No sticky queue, so a stopped worker can't hold a workflow task.
NO_CACHE: dict[str, Any] = {"max_cached_workflows": 0}


def seqs(stubs: Stubs) -> list[int]:
    return [change.seq for change in stubs.changes]


async def test_FR_001_03_restart_mid_verification() -> None:
    stubs = Stubs(result=ELIGIBLE)
    in_verification = asyncio.Event()

    async def hang_first_attempt(attempt: int) -> None:
        if attempt == 1:
            in_verification.set()
            await asyncio.Event().wait()  # the worker stops while this runs

    stubs.on_verify = hang_first_attempt
    async with environment() as env:
        async with worker(env.client, stubs, **NO_CACHE, graceful_shutdown_timeout=timedelta(0)):
            handle, _ = await start(env)
            await asyncio.wait_for(in_verification.wait(), timeout=30)

        async with worker(env.client, stubs, **NO_CACHE):
            await env.sleep(timedelta(minutes=5))  # past the verification attempt's timeout
            final = await asyncio.wait_for(handle.result(), timeout=30)

    assert final.outcome is CaseOutcome.approved
    assert stubs.verify_calls >= 2  # the interrupted step ran again, on the new worker
    assert seqs(stubs) == [1, 2, 3]  # each change recorded once


async def test_FR_001_03_restart_while_waiting_on_task_no_second_task() -> None:
    stubs = Stubs(result=INELIGIBLE)
    async with environment() as env:
        async with worker(env.client, stubs, **NO_CACHE):
            handle, _ = await start(env)
            waiting = await wait_for_state(handle, CaseState.under_review)
            assert waiting.open_review_task is not None

        async with worker(env.client, stubs, **NO_CACHE):
            resumed = await wait_for_state(handle, CaseState.under_review)
            assert resumed.open_review_task is not None
            await signal(
                handle,
                action(resumed.open_review_task.task_id, CaseworkerActionType.approve_denial),
            )
            final = await asyncio.wait_for(handle.result(), timeout=30)

    assert resumed.open_review_task.task_id == waiting.open_review_task.task_id
    opened = [
        c
        for c in stubs.changes
        if c.case_update is not None and c.case_update.state is CaseState.under_review
    ]
    assert len(opened) == 1
    assert final.outcome is CaseOutcome.denied
    assert seqs(stubs) == [1, 2, 3, 4]


async def test_FR_001_04_restart_after_approval_requests_no_second_notice() -> None:
    """The deciding change completes while no workflow worker runs; a new one finishes the case."""
    stubs = Stubs(result=ELIGIBLE)
    decided_seen = asyncio.Event()
    workflow_worker_stopped = asyncio.Event()

    async def hold_deciding_change(change: CaseChange) -> None:
        if change.event.to_state is CaseState.decided:
            decided_seen.set()
            await workflow_worker_stopped.wait()

    stubs.on_change = hold_deciding_change
    async with environment() as env:
        activity_worker = Worker(env.client, task_queue=TASK_QUEUE, activities=stubs.activities())

        def workflow_worker() -> Worker:
            return Worker(env.client, task_queue=TASK_QUEUE, workflows=[CaseWorkflow], **NO_CACHE)

        async with activity_worker:
            async with workflow_worker():
                handle, _ = await start(env)
                await asyncio.wait_for(decided_seen.wait(), timeout=30)
            workflow_worker_stopped.set()  # record_case_change(approved) now completes

            async with workflow_worker():
                final = await asyncio.wait_for(handle.result(), timeout=30)

    notices = [c.notice for c in stubs.changes if c.notice is not None]
    assert len(notices) == 1
    assert final.outcome is CaseOutcome.approved
    assert seqs(stubs) == [1, 2, 3]
