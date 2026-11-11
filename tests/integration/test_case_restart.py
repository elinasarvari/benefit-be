"""A real worker process killed mid-case (001; TC-75; TW-16, SC-001-01, FR-001-03, FR-001-04).

The worker runs as its own OS process (`tests/integration/case_worker_process.py`) against the
Temporal dev server and the replica-set MongoDB. It is killed with SIGKILL once the case waits on
its review task, and a second process finishes the case after the caseworker acts. Every effect
must exist exactly once: events (contiguous `seq`), the review task, the action and the notice.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from temporalio.client import Client, WorkflowFailureError
from temporalio.service import RPCError

from packages.domain.case import CaseState, CaseStatus
from packages.platform.settings import Settings
from tests.integration.case_stack import (
    CASEWORKER,
    INELIGIBLE_HOUSEHOLD,
    act,
    api,
    applicant_for,
    connect_temporal,
    documents,
    events,
    eventually,
    handle_for,
    mongo,
    new_task_queue,
    seeded_user,
    stack_settings,
    submit,
    wait_for_state,
    worker_process,
)
from tests.integration.fixtures import rebuild

RESTART_WAIT_S = 60.0  # the killed worker's sticky queue times out before the new one replays


async def answered_status(temporal: Client, case_id: str) -> CaseStatus | None:
    """The `status` query, once a live worker answers it."""
    try:
        return await asyncio.wait_for(
            handle_for(temporal, case_id).query("status", result_type=CaseStatus), timeout=10
        )
    except (TimeoutError, RPCError, WorkflowFailureError):
        return None


async def effects(settings: Settings, case_id: str) -> dict[str, list[dict[str, Any]]]:
    async with mongo(settings) as client:
        return {
            "events": await events(client, settings, case_id),
            "review_tasks": await documents(client, settings, "review_tasks", case_id),
            "caseworker_actions": await documents(client, settings, "caseworker_actions", case_id),
            "outbox": await documents(client, settings, "outbox", case_id),
            "coverage_periods": await documents(client, settings, "coverage_periods", case_id),
        }


async def test_FR_001_03_journey_worker_restart_no_duplicate_effects(
    integration_settings: Settings,
) -> None:
    await rebuild(integration_settings)
    settings = stack_settings(integration_settings, new_task_queue("restart"), demo_profile=False)
    temporal = await connect_temporal(settings)

    async with api(settings) as stack:
        applicant = stack.client_for(applicant_for(INELIGIBLE_HOUSEHOLD))
        caseworker = stack.client_for(seeded_user(CASEWORKER))

        async with worker_process(settings) as first:
            response = await submit(applicant, uuid.uuid4())
            assert response.status_code == 201, response.text
            case_id = str(response.json()["case_id"])
            waiting = await wait_for_state(applicant, case_id, CaseState.under_review)
            await first.kill()
        before_restart = await effects(settings, case_id)

        async with worker_process(settings):
            resumed = await eventually(
                lambda: answered_status(temporal, case_id),
                "the restarted worker to answer the status query",
                within=RESTART_WAIT_S,
            )
            assert resumed.state is CaseState.under_review
            assert resumed.open_review_task is not None
            task_id = waiting["open_review_task"]["task_id"]
            assert str(resumed.open_review_task.task_id) == task_id

            response = await act(caseworker, case_id, task_id, "approve_denial")
            assert response.status_code == 202, response.text
            decided = await wait_for_state(applicant, case_id, CaseState.decided)
            final = CaseStatus.model_validate(await handle_for(temporal, case_id).result())
        after = await effects(settings, case_id)

    assert final.outcome is not None and final.outcome.value == "denied"
    assert decided["outcome"] == "denied"
    # Nothing was written twice by the first worker, before or after it was killed.
    assert [e["seq"] for e in before_restart["events"]] == [1, 2, 3]
    assert len(before_restart["review_tasks"]) == 1
    assert [e["seq"] for e in after["events"]] == [1, 2, 3, 4]
    assert [e["to_state"] for e in after["events"]] == [
        "verifying",
        "determining",
        "under_review",
        "decided",
    ]
    assert [(t["_id"], t["status"]) for t in after["review_tasks"]] == [
        (uuid.UUID(task_id), "closed")
    ]
    assert len(after["caseworker_actions"]) == 1
    assert [(n["notice_type"], n["status"]) for n in after["outbox"]] == [("denial", "requested")]
    assert after["coverage_periods"] == []
