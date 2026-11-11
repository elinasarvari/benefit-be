"""Submission guards under concurrency (001; TC-68, TC-69; ADR-001-02, FR-001-23).

Real API app, replica-set MongoDB and Temporal dev server. No worker polls the test's task queue,
so the started workflows only have to exist; nothing runs them.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter

from temporalio.client import Client

from packages.domain.case import SubmissionRefusal, case_id_for, workflow_id_for
from packages.platform.settings import Settings
from tests.integration.case_stack import (
    ELIGIBLE_HOUSEHOLD,
    api,
    applicant_for,
    connect_temporal,
    eventually,
    mongo,
    new_task_queue,
    stack_settings,
    submit,
)
from tests.integration.fixtures import rebuild

PARALLEL = 10


async def executions(temporal: Client, workflow_id: str) -> int:
    return len([w async for w in temporal.list_workflows(f'WorkflowId = "{workflow_id}"')])


async def started_once(temporal: Client, workflow_id: str) -> int:
    async def probe() -> int | None:
        found = await executions(temporal, workflow_id)
        return found or None

    return await eventually(probe, f"workflow {workflow_id} to be visible")


async def test_FR_001_23_concurrent_same_submission_opens_one_case_one_workflow(
    integration_settings: Settings,
) -> None:
    await rebuild(integration_settings)
    settings = stack_settings(integration_settings, new_task_queue("submit"), demo_profile=False)
    temporal = await connect_temporal(settings)
    reference = uuid.uuid4()
    case_id = case_id_for(reference)

    async with api(settings) as stack:
        clients = [stack.client_for(applicant_for(ELIGIBLE_HOUSEHOLD)) for _ in range(PARALLEL)]
        responses = await asyncio.gather(*(submit(c, reference) for c in clients))

    codes = Counter(r.status_code for r in responses)
    assert codes == {201: 1, 200: PARALLEL - 1}, [r.text for r in responses]
    assert {r.json()["case_id"] for r in responses} == {str(case_id)}
    async with mongo(settings) as client:
        cases = client[settings.MONGO_DB]["cases"]
        assert await cases.count_documents({"submission_reference": str(reference)}) == 1
        assert await cases.count_documents({"household_id": ELIGIBLE_HOUSEHOLD}) == 1
    assert await started_once(temporal, workflow_id_for(case_id)) == 1


async def test_FR_001_23_concurrent_different_submissions_same_household_open_one(
    integration_settings: Settings,
) -> None:
    await rebuild(integration_settings)
    settings = stack_settings(integration_settings, new_task_queue("submit"), demo_profile=False)
    temporal = await connect_temporal(settings)
    sub_a, sub_b = uuid.uuid4(), uuid.uuid4()

    async with api(settings) as stack:
        first = stack.client_for(applicant_for(ELIGIBLE_HOUSEHOLD))
        second = stack.client_for(applicant_for(ELIGIBLE_HOUSEHOLD))
        responses = await asyncio.gather(submit(first, sub_a), submit(second, sub_b))

    by_code = {r.status_code: r for r in responses}
    assert sorted(by_code) == [201, 409], [r.text for r in responses]
    refusal = by_code[409].json()["error"]
    assert refusal["fields"][0]["type"] == SubmissionRefusal.household_has_open_case.value
    winner = uuid.UUID(by_code[201].json()["case_id"])
    loser = case_id_for(sub_b) if winner == case_id_for(sub_a) else case_id_for(sub_a)
    async with mongo(settings) as client:
        cases = client[settings.MONGO_DB]["cases"]
        assert await cases.count_documents({"household_id": ELIGIBLE_HOUSEHOLD, "open": True}) == 1
        assert await cases.count_documents({"_id": loser}) == 0
    assert await started_once(temporal, workflow_id_for(winner)) == 1
    assert await executions(temporal, workflow_id_for(loser)) == 0
