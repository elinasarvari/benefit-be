"""CaseWorkflow with the real determination activity over a failing store (T042; TC-99).

ADR-001-02 (Accepted, main): `determine_eligibility` runs with UNTIL_DONE, so an outage past 3
attempts is retried by the server until the store answers; the case is never failed or left
`determining` for good. Once the store recovers the real rules decide, and a non-eligible result
opens a review task (FR-001-16). TW-10, TW-13.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio.worker import Worker

from apps.worker.workflows.case import CaseWorkflow
from packages.domain.case import CaseState, ReviewReason
from tests.apps.worker.eligibility_fakes import (
    FakeSource,
    FakeStore,
    FakeVerified,
    activities,
    stored_member,
)
from tests.apps.worker.workflows.case_harness import (
    HOUSEHOLD,
    TASK_QUEUE,
    Stubs,
    environment,
    start,
    status_of,
    wait_for_state,
)

OUTAGE_ATTEMPTS = 5  # past RULES_STEP's 3 attempts


async def test_FR_003_02_real_determination_store_failing_past_retries_opens_review_and_does_not_stall() -> (  # noqa: E501
    None
):
    stubs = Stubs()
    source = FakeSource(
        members=[stored_member(state="WI", household_id=HOUSEHOLD)]
    )  # ELIG-01 fails
    store = FakeStore(failures=OUTAGE_ATTEMPTS)
    real = activities(source, FakeVerified(), store)
    others: list[Any] = [a for a in stubs.activities() if a.__name__ != "determine_eligibility"]

    async with environment() as env:
        async with Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[CaseWorkflow],
            activities=[*others, real.determine_eligibility],
        ):
            handle, _ = await start(env)
            await env.sleep(timedelta(minutes=5))  # time skips through the retry backoff
            current = await wait_for_state(handle, CaseState.under_review)
            final_status = await status_of(handle)

    assert store.attempts == OUTAGE_ATTEMPTS + 1  # retried until the store answered, once stored
    assert len(store.records) == 1
    assert current.state is CaseState.under_review  # not left in `determining`
    assert final_status.open_review_task is not None
    assert final_status.open_review_task.reason is ReviewReason.adverse_determination
