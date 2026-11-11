"""Re-record the CaseWorkflow histories used by test_case_replay.py (TW-14).

Run `uv run python -m tests.apps.worker.workflows.record_case_histories` only when the workflow's
command order changes on purpose; keep the old files as a separate replay test (TW-7).
"""

import asyncio
from pathlib import Path

from packages.domain.case import ActionType, CaseState
from tests.apps.worker.workflows.case_harness import (
    ELIGIBLE,
    INELIGIBLE,
    SYNTH_REASON,
    Stubs,
    action,
    environment,
    signal,
    start,
    wait_for_state,
    worker,
)

OUT = Path("tests/apps/worker/histories")


async def record(name: str, override: bool) -> None:
    stubs = Stubs(result=INELIGIBLE if override else ELIGIBLE)
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env)
        if override:
            waiting = await wait_for_state(handle, CaseState.under_review)
            assert waiting.open_review_task is not None
            await signal(
                handle,
                action(
                    waiting.open_review_task.task_id,
                    ActionType.override_to_approve,
                    reason=SYNTH_REASON,
                ),
            )
        await handle.result()
        history = await handle.fetch_history()
    (OUT / name).write_text(history.to_json())
    print(name, len(history.events), "events")


async def main() -> None:
    await record("case_approved.json", override=False)
    await record("case_override.json", override=True)


asyncio.run(main())
