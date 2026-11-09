from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

from temporalio import activity
from temporalio.client import WorkflowHandle
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from apps.worker.workflows.smoke import SmokeWorkflow
from packages.domain.smoke import SmokeInput

TASK_QUEUE = "test-restart"


@activity.defn(name="smoke_step")
async def stub_smoke_step(data: SmokeInput) -> str:
    return "done"


async def wait_for_timer(handle: WorkflowHandle) -> None:  # type: ignore[type-arg]
    """Return once the workflow has started its one-day timer."""
    for _ in range(100):
        history = await handle.fetch_history()
        if any(event.HasField("timer_started_event_attributes") for event in history.events):
            return
        await asyncio.sleep(0.1)
    raise AssertionError("the workflow never started its timer")


async def test_smoke_workflow_resumes_after_worker_restart() -> None:
    case_id = uuid.uuid4()
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        async with Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[SmokeWorkflow],
            activities=[stub_smoke_step],
            max_cached_workflows=0,  # no sticky queue, so a stopped worker can't hold a task
        ):
            handle = await env.client.start_workflow(
                SmokeWorkflow.run,
                SmokeInput(case_id=case_id),
                id=f"case-{case_id}",
                task_queue=TASK_QUEUE,
            )
            await wait_for_timer(handle)

        # The first worker is gone. A new one must pick the workflow up where it stopped.
        async with Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[SmokeWorkflow],
            activities=[stub_smoke_step],
            max_cached_workflows=0,  # no sticky queue, so a stopped worker can't hold a task
        ):
            await env.sleep(timedelta(days=2))  # jump past the one-day timer
            result = await asyncio.wait_for(handle.result(), timeout=30)

    assert result.activity_result == "done"
