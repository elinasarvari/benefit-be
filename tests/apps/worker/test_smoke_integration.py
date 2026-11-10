"""The worker runs the smoke workflow against a real local Temporal server (FR-000-17).

Needs a Temporal dev server: `make dev` starts one, or run `temporal server start-dev`.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from apps.worker.activities.smoke import smoke_step
from apps.worker.workflows.smoke import SmokeWorkflow
from packages.domain.smoke import SmokeInput
from packages.platform.settings import Settings

pytestmark = pytest.mark.integration


async def test_FR_000_17_worker_runs_smoke_against_local_temporal(settings: Settings) -> None:
    try:
        client = await asyncio.wait_for(
            Client.connect(
                settings.TEMPORAL_ADDRESS,
                namespace=settings.TEMPORAL_NAMESPACE,
                data_converter=pydantic_data_converter,
            ),
            timeout=5,
        )
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"Temporal is not reachable at {settings.TEMPORAL_ADDRESS}: {exc!r}")
    case_id = uuid.uuid4()
    task_queue = f"test-smoke-{case_id}"

    async with Worker(
        client, task_queue=task_queue, workflows=[SmokeWorkflow], activities=[smoke_step]
    ):
        result = await client.execute_workflow(
            SmokeWorkflow.run,
            SmokeInput(case_id=case_id, delay_seconds=1),
            id=f"case-{case_id}",
            task_queue=task_queue,
        )

    assert result.activity_result == f"smoke ok for {case_id}"
