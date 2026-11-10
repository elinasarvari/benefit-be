from __future__ import annotations

import io
import json
import uuid
from collections.abc import Iterator

import pytest
import structlog
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from apps.api.temporal_client import RequestIdInterceptor
from apps.worker.activities.smoke import smoke_step
from apps.worker.interceptors import ContextInterceptor
from apps.worker.workflows.smoke import SmokeWorkflow
from packages.domain.smoke import SmokeInput
from packages.platform.logging import bind_context, clear_context, configure_logging
from packages.platform.settings import Settings

TASK_QUEUE = "test-interceptors"


@pytest.fixture
def stream(settings: Settings) -> Iterator[io.StringIO]:
    buffer = io.StringIO()
    configure_logging(settings, "worker", stream=buffer)
    yield buffer
    clear_context()


async def run_smoke(case_id: uuid.UUID) -> None:
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        client = Client(**{**env.client.config(), "interceptors": [RequestIdInterceptor()]})
        async with Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[SmokeWorkflow],
            activities=[smoke_step],
            interceptors=[ContextInterceptor()],
        ):
            await client.execute_workflow(
                SmokeWorkflow.run,
                SmokeInput(case_id=case_id, delay_seconds=1),
                id=f"case-{case_id}",
                task_queue=TASK_QUEUE,
            )


def smoke_record(stream: io.StringIO) -> dict[str, object]:
    return next(json.loads(line) for line in stream.getvalue().splitlines() if "smoke.step" in line)


async def test_FR_000_10_activity_log_includes_case_and_workflow_id(stream: io.StringIO) -> None:
    case_id = uuid.uuid4()

    await run_smoke(case_id)

    record = smoke_record(stream)
    assert record["case_id"] == str(case_id)
    assert record["workflow_id"] == f"case-{case_id}"
    assert record["activity_type"] == "smoke_step"
    assert record["attempt"] == 1
    assert record["component"] == "worker"


async def test_FR_000_10_request_id_header_propagated_from_client_to_activity_logs(
    stream: io.StringIO,
) -> None:
    bind_context(request_id="3f0c2a9e-4b1d-4e55-9a51-7f2f8f5f1c11")

    await run_smoke(uuid.uuid4())

    assert smoke_record(stream)["request_id"] == "3f0c2a9e-4b1d-4e55-9a51-7f2f8f5f1c11"
    assert (
        "request_id" in structlog.contextvars.get_contextvars()
    )  # the caller's context is untouched


def test_the_two_request_id_header_constants_stay_equal() -> None:
    from apps.worker import interceptors, workflow_interceptor
    from packages.platform import logging as platform_logging

    # The workflow module can't import the platform one inside the sandbox, so keep a copy.
    assert workflow_interceptor.REQUEST_ID_HEADER == platform_logging.REQUEST_ID_HEADER
    assert interceptors.REQUEST_ID_HEADER == platform_logging.REQUEST_ID_HEADER
