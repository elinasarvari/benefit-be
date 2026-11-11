"""Seeded households A/B/C through the real engine, workflow and API (003 [qa]; TC-129, TC-130).

Real API app, replica-set MongoDB, Temporal dev server and an in-process worker whose determination
activity is the real one (`DeterminationActivities` over the Mongo snapshot, verified-facts and
determination repositories, ADR-003-02, -03), not 001's stub. Written test-first: it fails until
T020–T026 land and the integrator has merged `origin/main` (ADR-003-04). All data is synthetic.
"""

from __future__ import annotations

import uuid
from typing import Any

from packages.domain.case import CaseState
from packages.platform.settings import Settings
from tests.integration.case_stack import (
    API,
    CANNOT_DETERMINE_HOUSEHOLD,
    CASEWORKER,
    ELIGIBLE_HOUSEHOLD,
    INELIGIBLE_HOUSEHOLD,
    api,
    applicant_for,
    connect_temporal,
    eventually,
    get_case,
    mongo,
    new_task_queue,
    seeded_user,
    stack_settings,
    submit,
)
from tests.integration.fixtures import rebuild

EXPECTED = [
    (ELIGIBLE_HOUSEHOLD, "eligible", None),
    (INELIGIBLE_HOUSEHOLD, "ineligible", "income_over_limit"),
    (CANNOT_DETERMINE_HOUSEHOLD, "cannot_determine", None),
]


def _real_worker(settings: Settings, temporal: Any) -> Any:
    """`case_worker` with the real determination activity, wired as `apps.worker.main` does."""
    from contextlib import asynccontextmanager

    from temporalio.worker import Worker

    from apps.worker.main import build_activities
    from apps.worker.workflows.case import CaseWorkflow
    from packages.platform.clock import SystemClock
    from packages.platform.mongo import make_client

    @asynccontextmanager
    async def run() -> Any:
        client = make_client(settings.MONGO_URL_APP.get_secret_value())
        try:
            async with Worker(
                temporal,
                task_queue=settings.TEMPORAL_TASK_QUEUE,
                workflows=[CaseWorkflow],
                activities=build_activities(settings, SystemClock(), client),
            ):
                yield
        finally:
            await client.close()

    return run()


async def _submit_and_settle(
    settings: Settings, temporal: Any, household_id: uuid.UUID
) -> tuple[str, Any, Any]:
    async with api(settings) as stack:
        applicant = stack.client_for(applicant_for(household_id))
        caseworker = stack.client_for(seeded_user(CASEWORKER))
        async with _real_worker(settings, temporal):
            response = await submit(applicant)
            assert response.status_code == 201, response.text
            case_id: str = response.json()["case_id"]
            settled = (CaseState.under_review.value, CaseState.decided.value)

            async def left_determining() -> dict[str, Any] | None:
                body = await get_case(caseworker, case_id)
                return body if body["state"] in settled else None

            await eventually(left_determining, f"case {case_id} left determining")
            listed = await caseworker.get(f"{API}/cases/{case_id}/determinations")
    return case_id, response, listed


async def test_FR_003_14_seeded_households_a_b_c_submit_form_and_end_eligible_ineligible_cannot_determine(  # noqa: E501 - the name starts with the FR ID and the TC's scenario
    integration_settings: Settings,
) -> None:
    await rebuild(integration_settings)
    settings = stack_settings(integration_settings, new_task_queue("determine"), demo_profile=False)
    temporal = await connect_temporal(settings)

    for household_id, result, reason in EXPECTED:
        _, _, listed = await _submit_and_settle(settings, temporal, household_id)

        assert listed.status_code == 200, listed.text
        newest = listed.json()["items"][0]
        assert newest["result"] == result
        assert newest["reason_code"] == reason
        assert len(newest["rules"]) == 6


async def test_FR_003_23_determination_stored_by_workflow_is_readable_through_api(
    integration_settings: Settings,
) -> None:
    await rebuild(integration_settings)
    settings = stack_settings(integration_settings, new_task_queue("determine"), demo_profile=False)
    temporal = await connect_temporal(settings)

    case_id, _, listed = await _submit_and_settle(settings, temporal, INELIGIBLE_HOUSEHOLD)

    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert len(items) == 1
    view = items[0]
    async with mongo(settings) as client:
        db = client[settings.MONGO_DB]
        record = await db["determinations"].find_one({"_id": uuid.UUID(view["determination_id"])})
        assert record is not None, "the API returned a determination that is not stored"
        # DeterminationRecord (data-model.md): the engine output sits under `determination`.
        stored = record["determination"]
        snapshot = await db["household_snapshots"].find_one(
            {"_id": record["snapshot_id"], "case_id": uuid.UUID(case_id)}
        )
    assert snapshot is not None, "no snapshot was captured for the determination (FR-003-19)"
    assert snapshot["as_of_date"] == stored["as_of_date"]
    assert view["result"] == stored["result"]
    assert view["rule_set_id"] == stored["rule_set_id"]
    assert view["rule_set_version"] == stored["rule_set_version"]
    assert [r["rule_id"] for r in view["rules"]] == [r["rule_id"] for r in stored["rule_results"]]
    assert view["household_size"] == len(snapshot["members"])
    assert str(stored["household_monthly_income"]) == view["household_monthly_income"]
