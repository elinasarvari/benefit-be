"""API -> worker -> MongoDB journeys of one case (001; TC-71–TC-74, TC-76, TC-77).

Real API app, replica-set MongoDB, Temporal dev server and an in-process worker with the real
`record_case_change`; the verification and determination stubs answer by the seeded household
(applicant1 eligible, applicant2 ineligible, applicant3 cannot_determine; research R1). A notice
is only "requested" in 001: it is asserted as an `outbox` record (spec Assumptions).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import httpx
from temporalio.client import Client, WorkflowExecutionStatus

from packages.domain.case import CaseState, CaseStatus
from packages.platform.settings import Settings
from tests.integration.case_stack import (
    CANNOT_DETERMINE_HOUSEHOLD,
    CASEWORKER,
    ELIGIBLE_HOUSEHOLD,
    INELIGIBLE_HOUSEHOLD,
    SYNTH_REASON,
    Api,
    act,
    api,
    applicant_for,
    as_date,
    case_document,
    case_worker,
    connect_temporal,
    documents,
    events,
    expected_coverage,
    handle_for,
    mongo,
    new_task_queue,
    seeded_user,
    stack_settings,
    submit,
    utc,
    wait_for_state,
)
from tests.integration.fixtures import rebuild

DEMO_SCALE = 1440


@dataclass
class Journey:
    settings: Settings
    temporal: Client
    stack: Api
    caseworker: httpx.AsyncClient

    async def open_case(self, household_id: uuid.UUID) -> tuple[httpx.AsyncClient, str]:
        applicant = self.stack.client_for(applicant_for(household_id))
        response = await submit(applicant)
        assert response.status_code == 201, response.text
        assert response.json()["state"] == CaseState.submitted.value
        return applicant, str(response.json()["case_id"])

    async def docs(self, collection: str, case_id: str) -> list[dict[str, Any]]:
        async with mongo(self.settings) as client:
            return await documents(client, self.settings, collection, case_id)

    async def case(self, case_id: str) -> dict[str, Any]:
        async with mongo(self.settings) as client:
            return await case_document(client, self.settings, case_id)

    async def events(self, case_id: str) -> list[dict[str, Any]]:
        async with mongo(self.settings) as client:
            return await events(client, self.settings, case_id)

    async def stop(self, case_id: str) -> None:
        """Tidy a case left waiting on a review task off the dev server."""
        handle = handle_for(self.temporal, case_id)
        if (await handle.describe()).status is WorkflowExecutionStatus.RUNNING:
            await handle.terminate("test finished")


@asynccontextmanager
async def journey(base: Settings, *, demo_profile: bool = False) -> AsyncIterator[Journey]:
    await rebuild(base)
    settings = stack_settings(base, new_task_queue("journey"), demo_profile=demo_profile)
    temporal = await connect_temporal(settings)
    async with case_worker(settings, temporal), api(settings) as stack:
        yield Journey(settings, temporal, stack, stack.client_for(seeded_user(CASEWORKER)))


def state_path(case_events: list[dict[str, Any]]) -> list[str]:
    return [e["to_state"] for e in case_events if e["kind"] == "state_changed"]


async def test_FR_001_01_journey_submit_to_approved_with_coverage_and_notice(
    integration_settings: Settings,
) -> None:
    async with journey(integration_settings) as j:
        applicant, case_id = await j.open_case(ELIGIBLE_HOUSEHOLD)
        decided = await wait_for_state(applicant, case_id, CaseState.decided)
        case = await j.case(case_id)
        coverage = await j.docs("coverage_periods", case_id)
        outbox = await j.docs("outbox", case_id)
        case_events = await j.events(case_id)

    received_on = date.fromisoformat(decided["received_on"])
    assert decided["outcome"] == "approved"
    assert decided["days_remaining"] is None
    assert date.fromisoformat(decided["decision_deadline"]) == received_on + timedelta(days=45)
    assert (case["state"], case["open"], case["outcome"]) == ("decided", False, "approved")
    assert len(coverage) == 1
    assert coverage[0]["household_id"] == ELIGIBLE_HOUSEHOLD
    assert coverage[0]["status"] == "active"
    assert (as_date(coverage[0]["start_date"]), as_date(coverage[0]["end_date"])) == (
        expected_coverage(received_on)
    )
    assert [(n["notice_type"], n["status"]) for n in outbox] == [("approval", "requested")]
    assert [e["seq"] for e in case_events] == list(range(1, len(case_events) + 1))
    assert state_path(case_events) == ["verifying", "determining", "decided"]


async def test_FR_001_09_journey_ineligible_approve_denial_decides_denied(
    integration_settings: Settings,
) -> None:
    async with journey(integration_settings) as j:
        applicant, case_id = await j.open_case(INELIGIBLE_HOUSEHOLD)
        waiting = await wait_for_state(applicant, case_id, CaseState.under_review)
        task = waiting["open_review_task"]
        assert task is not None and task["reason"] == "adverse_determination"
        assert "approve_denial" in task["allowed_actions"]

        response = await act(j.caseworker, case_id, task["task_id"], "approve_denial")
        assert response.status_code == 202, response.text
        decided = await wait_for_state(applicant, case_id, CaseState.decided)
        tasks = await j.docs("review_tasks", case_id)
        actions = await j.docs("caseworker_actions", case_id)
        outbox = await j.docs("outbox", case_id)
        coverage = await j.docs("coverage_periods", case_id)

    assert (decided["outcome"], decided["denial_reason"]) == ("denied", "income_over_limit")
    assert decided["open_review_task"] is None
    assert [(t["status"], t["resolution"]["action"]) for t in tasks] == [
        ("resolved", "approve_denial")
    ]
    assert [(a["outcome"], a["reviewer_id"]) for a in actions] == [
        ("applied", seeded_user(CASEWORKER).id)
    ]
    assert [(n["notice_type"], n["status"]) for n in outbox] == [("denial", "requested")]
    assert coverage == []


async def test_FR_001_20_journey_override_to_approve_keeps_determination(
    integration_settings: Settings,
) -> None:
    async with journey(integration_settings) as j:
        applicant, case_id = await j.open_case(INELIGIBLE_HOUSEHOLD)
        waiting = await wait_for_state(applicant, case_id, CaseState.under_review)
        task_id = waiting["open_review_task"]["task_id"]

        response = await act(
            j.caseworker, case_id, task_id, "override_to_approve", reason=SYNTH_REASON
        )
        assert response.status_code == 202, response.text
        decided = await wait_for_state(applicant, case_id, CaseState.decided)
        case = await j.case(case_id)
        tasks = await j.docs("review_tasks", case_id)
        coverage = await j.docs("coverage_periods", case_id)
        outbox = await j.docs("outbox", case_id)

    assert decided["outcome"] == "approved"
    override = case["override"]
    assert override["reason"] == SYNTH_REASON
    assert override["reviewer_id"] == seeded_user(CASEWORKER).id
    # The determination is referenced, never rewritten: the task's evidence still says ineligible.
    (task,) = tasks
    assert override["determination_id"] == task["evidence"]["determination_id"]
    assert task["evidence"]["failed_rules"] == ["income_over_limit"]
    assert task["resolution"]["action"] == "override_to_approve"
    assert len(coverage) == 1
    assert [n["notice_type"] for n in outbox] == ["approval"]


async def test_FR_001_16_journey_cannot_determine_waits_on_task(
    integration_settings: Settings,
) -> None:
    async with journey(integration_settings) as j:
        applicant, case_id = await j.open_case(CANNOT_DETERMINE_HOUSEHOLD)
        try:
            waiting = await wait_for_state(applicant, case_id, CaseState.under_review)
            await asyncio.sleep(2)  # nothing may decide it without a person
            still = await wait_for_state(applicant, case_id, CaseState.under_review)
            tasks = await j.docs("review_tasks", case_id)
            case = await j.case(case_id)
            outbox = await j.docs("outbox", case_id)
            coverage = await j.docs("coverage_periods", case_id)
            running = (await handle_for(j.temporal, case_id).describe()).status
        finally:
            await j.stop(case_id)

    assert waiting["open_review_task"]["reason"] == "cannot_determine"
    assert still["outcome"] is None
    assert (case["state"], case["open"], case.get("outcome")) == ("under_review", True, None)
    (task,) = tasks
    assert (task["status"], task["reason"]) == ("open", "cannot_determine")
    assert task["evidence"]["missing_items"] == ["pay_stub"]
    assert outbox == [] and coverage == []
    assert running is WorkflowExecutionStatus.RUNNING


async def test_FR_001_11_api_status_matches_workflow_status_query(
    integration_settings: Settings,
) -> None:
    async with journey(integration_settings) as j:
        applicant, case_id = await j.open_case(INELIGIBLE_HOUSEHOLD)
        try:
            from_api = await wait_for_state(applicant, case_id, CaseState.under_review)
            from_workflow = await handle_for(j.temporal, case_id).query(
                "status", result_type=CaseStatus
            )
        finally:
            await j.stop(case_id)

    # The API returns `CaseDetail` (CaseStatus plus case_type, coverage, override, demo_clock).
    expected = CaseStatus.model_validate(from_workflow).model_dump(mode="json")
    assert {k: from_api[k] for k in expected} == expected
    assert from_api["days_remaining"] == 45
    assert from_api["at_risk"] is False


async def test_FR_001_13_demo_profile_history_has_case_and_real_times(
    integration_settings: Settings,
) -> None:
    async with journey(integration_settings, demo_profile=True) as j:
        applicant, case_id = await j.open_case(ELIGIBLE_HOUSEHOLD)
        await wait_for_state(applicant, case_id, CaseState.decided)
        case = await j.case(case_id)
        case_events = await j.events(case_id)

    clock = case["clock"]
    assert clock["scale"] == DEMO_SCALE
    real_start, case_start = utc(clock["real_start"]), utc(clock["case_start"])
    assert len(case_events) >= 3
    for event in case_events:
        case_time, real_time = utc(event["case_time"]), utc(event["real_time"])
        expected = (
            case_start
            + (real_time - real_start) * DEMO_SCALE
            + timedelta(seconds=clock.get("offset_seconds", 0))
        )
        # MongoDB keeps milliseconds, so the scaled real time may be off by up to 1440 ms.
        assert abs(case_time - expected) <= timedelta(seconds=3), event["seq"]
    last = case_events[-1]
    assert utc(last["case_time"]) - utc(last["real_time"]) > timedelta(seconds=1)
