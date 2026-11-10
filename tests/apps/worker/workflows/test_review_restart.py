"""The open task and its timers survive a worker restart (TW-16, FR-010-01, FR-010-02)."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from apps.worker.workflows.review_harness import HarnessScenario, ReviewHarnessWorkflow
from apps.worker.workflows.review_ports import CaseStep
from packages.domain.review import ActionType, ReviewFlag
from tests.apps.worker.review_support import action_signal
from tests.apps.worker.workflows.review_env import (
    TASK_QUEUE,
    Stubs,
    harness_input,
    wait_for_open_tasks,
)


def worker(env: WorkflowEnvironment, stubs: Stubs) -> Worker:
    return Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[ReviewHarnessWorkflow],
        activities=stubs.activities(),
        max_cached_workflows=0,  # no sticky queue, so a stopped worker can't hold a task
    )


async def test_FR_010_01_open_task_and_timers_survive_worker_restart() -> None:
    stubs = Stubs()
    data = harness_input(HarnessScenario.determine)

    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        async with worker(env, stubs):
            handle = await env.client.start_workflow(
                ReviewHarnessWorkflow.run, data, id=f"case-{data.case_id}", task_queue=TASK_QUEUE
            )
            [task] = await wait_for_open_tasks(handle)
            for _ in range(200):
                if stubs.upserts:
                    break
                await asyncio.sleep(0.05)

        # First worker gone. A new one keeps the task, fires the reminder, applies the action.
        async with worker(env, stubs):
            await env.sleep(timedelta(days=2, minutes=1))
            for _ in range(200):
                if stubs.flags:
                    break
                await asyncio.sleep(0.05)
            await handle.signal(
                ReviewHarnessWorkflow.caseworker_action,
                action_signal(task.task_id, ActionType.approve_denial),
            )
            result = await asyncio.wait_for(handle.result(), timeout=30)

    assert len(stubs.upserts) == 1
    assert [u.flag for u in stubs.flags] == [ReviewFlag.reminder]
    assert [r.step for r in result.results] == [CaseStep.decided_denied]
    assert [c.task_id for c in stubs.closes] == [task.task_id]
