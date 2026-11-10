"""A real worker process for the restart test (TC-75, TW-16).

Run as `python -m tests.integration.case_worker_process`.

The same workflows, activities, interceptors and Temporal connection as `apps.worker.main`, except
that the determination stub answers by the seeded household result (research R1), so the test can
stop the process while a case waits on a review task. `apps.worker.main` still wires the
all-eligible stub (specs/001-case-lifecycle/lanes/workflows-lane/blocked.md, item 1).
"""

from __future__ import annotations

import asyncio
import sys

from temporalio.worker import Worker

from apps.worker.interceptors import ContextInterceptor
from apps.worker.main import WORKFLOWS, connect_with_backoff
from packages.platform.logging import configure_logging
from packages.platform.mongo import make_client
from packages.platform.settings import load_settings
from tests.integration.case_stack import case_activities


async def run() -> None:
    settings = load_settings()
    configure_logging(settings, "worker")
    mongo = make_client(settings.MONGO_URL_APP.get_secret_value())
    try:
        client = await connect_with_backoff(settings)
        await Worker(
            client,
            task_queue=settings.TEMPORAL_TASK_QUEUE,
            workflows=WORKFLOWS,
            activities=case_activities(settings, mongo),
            interceptors=[ContextInterceptor()],
        ).run()
    finally:
        await mongo.close()


if __name__ == "__main__":
    asyncio.run(run())
    sys.exit(0)
