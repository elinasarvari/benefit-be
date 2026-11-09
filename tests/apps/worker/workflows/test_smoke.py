from __future__ import annotations

import uuid
from datetime import UTC, datetime

from temporalio import activity
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from apps.worker.workflows.calendar import chicago_date
from apps.worker.workflows.smoke import SmokeWorkflow
from packages.domain.smoke import SmokeInput

TASK_QUEUE = "test-smoke"


@activity.defn(name="smoke_step")
async def stub_smoke_step(data: SmokeInput) -> str:
    return f"stubbed {data.case_id}"


async def test_FR_000_27_smoke_workflow_timer_fires_and_uses_chicago_date() -> None:
    case_id = uuid.uuid4()
    before = chicago_date(datetime.now(UTC))

    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        async with Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[SmokeWorkflow],
            activities=[stub_smoke_step],
        ):
            result = await env.client.execute_workflow(
                SmokeWorkflow.run,
                SmokeInput(case_id=case_id),
                id=f"case-{case_id}",
                task_queue=TASK_QUEUE,
            )

    after = chicago_date(datetime.now(UTC))
    assert result.started_on in {before, after}
    assert result.activity_result == f"stubbed {case_id}"
