"""Temporal worker entry point: `python -m apps.worker.main`.

Start-up order: settings, logging, health server, Temporal connection, worker. Temporal being down
is not a start-up failure: the health server reports the worker as not ready while it retries.
"""

from __future__ import annotations

import asyncio
import sys

import structlog
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from apps.worker.activities.agent_step import AgentStepActivities, smoke_tool
from apps.worker.activities.smoke import smoke_step
from apps.worker.health import WorkerState, worker_checks
from apps.worker.interceptors import ContextInterceptor
from apps.worker.workflows.smoke import SmokeWorkflow
from packages.platform.clock import SystemClock
from packages.platform.health import serve_health
from packages.platform.logging import configure_logging
from packages.platform.settings import Settings, load_settings

log = structlog.get_logger()
MAX_BACKOFF_S = 30.0
CONNECT_TIMEOUT_S = 5.0


async def connect_with_backoff(settings: Settings) -> Client:
    delay = 1.0
    while True:
        try:
            return await asyncio.wait_for(
                Client.connect(
                    settings.TEMPORAL_ADDRESS,
                    namespace=settings.TEMPORAL_NAMESPACE,
                    data_converter=pydantic_data_converter,
                ),
                timeout=CONNECT_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001 - keep retrying; readiness reports not ready meanwhile
            log.warning("temporal.connect.failed", error=type(exc).__name__, retry_in_s=delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_BACKOFF_S)


async def run_worker(settings: Settings) -> None:
    state = WorkerState()
    server = serve_health("worker", settings.WORKER_HEALTH_PORT, worker_checks(settings, state))
    health_task = asyncio.create_task(server.serve())
    try:
        client = await connect_with_backoff(settings)
        clock = SystemClock()
        agent_steps = AgentStepActivities(settings, clock, smoke_tool(settings, clock))
        worker = Worker(
            client,
            task_queue=settings.TEMPORAL_TASK_QUEUE,
            workflows=[SmokeWorkflow],
            activities=[smoke_step, agent_steps.run],
            interceptors=[ContextInterceptor()],
        )
        state.polling = True
        log.info("worker.started", task_queue=settings.TEMPORAL_TASK_QUEUE)
        await worker.run()
    finally:
        state.polling = False
        server.should_exit = True
        await health_task


def main() -> int:
    settings = load_settings()
    configure_logging(settings, "worker")
    asyncio.run(run_worker(settings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
