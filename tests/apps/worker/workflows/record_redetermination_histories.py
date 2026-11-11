"""Record the histories used by test_redetermination_replay.py (TW-14).

Run `uv run python -m tests.apps.worker.workflows.record_redetermination_histories` only when the
command order of the redetermination path or of RedeterminationsDueWorkflow changes on purpose;
keep the old files as a separate replay test (TW-7). All data is synthetic.
"""

import asyncio
import uuid
from pathlib import Path

from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment

from packages.domain.renewal import DueRedeterminationsRequest
from tests.apps.worker.workflows.case_harness import ELIGIBLE, environment
from tests.apps.worker.workflows.redetermination_harness import (
    OUT_OF_DATE,
    RenewalStubs,
    reach_awaiting_applicant,
    response,
    send_response,
    start,
    worker,
)
from tests.apps.worker.workflows.test_redeterminations_due import (
    QUEUE,
    TODAY,
    FakeCases,
    FixedClock,
    StubCaseWorkflow,
    period,
)

OUT = Path("tests/apps/worker/histories")


async def record_case() -> None:
    stubs = RenewalStubs(result=ELIGIBLE, items=OUT_OF_DATE)
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env, stubs)
        notice_id = await reach_awaiting_applicant(handle, stubs)
        await send_response(handle, response(notice_id))
        await handle.result()
        history = await handle.fetch_history()
    (OUT / "redetermination_request_approved.json").write_text(history.to_json())
    print("redetermination_request_approved.json", len(history.events), "events")


async def record_due() -> None:
    from temporalio.worker import UnsandboxedWorkflowRunner, Worker

    from apps.worker.activities.redeterminations_due import DueRedeterminationActivities
    from apps.worker.workflows.redeterminations_due import RedeterminationsDueWorkflow
    from tests.apps.worker.workflows.test_redeterminations_due import FakePeriods

    periods = [period(30), period(59)]
    activities = DueRedeterminationActivities(
        FakePeriods(periods), FakeCases(), FixedClock(), False
    )
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        async with Worker(
            env.client,
            task_queue=QUEUE,
            workflow_runner=UnsandboxedWorkflowRunner(),
            workflows=[RedeterminationsDueWorkflow, StubCaseWorkflow],
            activities=[activities.open_due_redeterminations],
        ):
            handle = await env.client.start_workflow(
                RedeterminationsDueWorkflow.run,
                DueRedeterminationsRequest(as_of_date=TODAY),
                id=f"due-{uuid.uuid4()}",
                task_queue=QUEUE,
            )
            await handle.result()
            history = await handle.fetch_history()
    (OUT / "redeterminations_due.json").write_text(history.to_json())
    print("redeterminations_due.json", len(history.events), "events")


async def main() -> None:
    await record_case()
    await record_due()


asyncio.run(main())
