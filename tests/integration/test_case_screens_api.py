"""The case screens' API on the real stack (001; TC-115–TC-121; ADR-001-07, -08, -09, R12, R21).

Real API app, replica-set MongoDB and Temporal dev server. Tests that need a case to move run an
in-process worker (stubs answer by the seeded household: A eligible, B ineligible, C cannot
determine); the others leave the task queue unpolled so the started workflows only have to exist.
All data is synthetic (PR-3).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from packages.domain.auth import Role, UserPublic
from packages.domain.case import CaseState, SubmissionRefusal, case_id_for
from packages.platform.settings import Settings
from tests.apps.api.forms import SYNTH_EMPLOYER, SYNTH_NAME, form_dict
from tests.integration.case_stack import (
    API,
    CASEWORKER,
    ELIGIBLE_HOUSEHOLD,
    INELIGIBLE_HOUSEHOLD,
    Api,
    api,
    applicant_for,
    case_worker,
    connect_temporal,
    mongo,
    new_task_queue,
    seeded_user,
    stack_settings,
    submit,
    utc,
    wait_for_state,
)
from tests.integration.fixtures import rebuild


@dataclass
class Screens:
    settings: Settings
    stack: Api

    @property
    def caseworker(self) -> httpx.AsyncClient:
        return self.stack.client_for(seeded_user(CASEWORKER))

    def applicant(self, household_id: uuid.UUID) -> httpx.AsyncClient:
        return self.stack.client_for(applicant_for(household_id))

    def new_household_applicant(self) -> tuple[httpx.AsyncClient, uuid.UUID]:
        """An applicant of a household with no seeded stub result (research R21)."""
        household_id = uuid.uuid4()
        user = UserPublic(
            id=uuid.uuid4(),
            username=f"synth-{household_id.hex[:8]}",
            display_name="Synthetic Applicant",
            role=Role.applicant,
            household_id=household_id,
        )
        return self.stack.client_for(user), household_id

    async def documents(self, collection: str, query: dict[str, Any]) -> list[dict[str, Any]]:
        async with mongo(self.settings) as client:
            return [d async for d in client[self.settings.MONGO_DB][collection].find(query)]

    async def audit(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        async with mongo(self.settings) as client:
            return [d async for d in client[self.settings.MONGO_AUDIT_DB]["audit_log"].find(query)]


@asynccontextmanager
async def screens(base: Settings, *, worker: bool) -> AsyncIterator[Screens]:
    await rebuild(base)
    settings = stack_settings(base, new_task_queue("screens"), demo_profile=False)
    temporal = await connect_temporal(settings)
    async with api(settings) as stack:
        if worker:
            async with case_worker(settings, temporal):
                yield Screens(settings, stack)
        else:
            yield Screens(settings, stack)


async def test_FR_001_24_double_submit_writes_one_application_and_refusal_writes_none(
    integration_settings: Settings,
) -> None:
    async with screens(integration_settings, worker=False) as s:
        applicant = s.applicant(ELIGIBLE_HOUSEHOLD)
        reference = uuid.uuid4()
        first = await submit(applicant, reference)
        second = await submit(applicant, reference)
        refused = await submit(applicant, uuid.uuid4())
        # The seed holds other households' history applications (FR-003-22); count this one's.
        stored = await s.documents("applications", {"household_id": ELIGIBLE_HOUSEHOLD})

    assert (first.status_code, second.status_code, refused.status_code) == (201, 200, 409)
    assert [d["_id"] for d in stored] == [case_id_for(reference)]


async def test_FR_001_24_stored_application_matches_submitted_form(
    integration_settings: Settings,
) -> None:
    async with screens(integration_settings, worker=False) as s:
        reference = uuid.uuid4()
        response = await submit(s.applicant(ELIGIBLE_HOUSEHOLD), reference)
        assert response.status_code == 201, response.text
        mine = {"household_id": ELIGIBLE_HOUSEHOLD}  # the seed holds other households' history
        (stored,) = await s.documents("applications", mine)
        (case,) = await s.documents("cases", mine)

    expected = form_dict()
    assert stored["synthetic"] is True
    assert stored["household_id"] == ELIGIBLE_HOUSEHOLD
    assert stored["submission_reference"] == str(reference)
    assert [m["full_name"] for m in stored["form"]["members"]] == [SYNTH_NAME, "SYNTH-SPOUSE"]
    assert stored["form"]["members"][0]["jobs"][0]["employer"] == SYNTH_EMPLOYER
    assert stored["form"]["members"][1]["zero_income"] is True
    assert stored["form"]["address"] == expected["address"]
    # submitted_at is the case clock's time of the submit (scale 1: the clock starts at submit)
    started = utc(case["clock"]["case_start"])
    assert abs(utc(stored["submitted_at"]) - started) < timedelta(seconds=5)


async def test_FR_001_25_refusals_return_related_case_id_end_to_end(
    integration_settings: Settings,
) -> None:
    async with screens(integration_settings, worker=True) as s:
        # H1 (household B) keeps an open case under review; H3 (household A) gets coverage.
        open_applicant = s.applicant(INELIGIBLE_HOUSEHOLD)
        opened = await submit(open_applicant)
        open_case_id = opened.json()["case_id"]
        await wait_for_state(open_applicant, open_case_id, CaseState.under_review)
        open_refusal = await submit(open_applicant)

        covered_applicant = s.applicant(ELIGIBLE_HOUSEHOLD)
        approved = await submit(covered_applicant)
        covered_case_id = approved.json()["case_id"]
        await wait_for_state(covered_applicant, covered_case_id, CaseState.decided)
        coverage_refusal = await submit(covered_applicant)

    assert open_refusal.status_code == 409
    error = open_refusal.json()["error"]
    assert error["fields"][0]["type"] == SubmissionRefusal.household_has_open_case.value
    assert error["related_case_id"] == open_case_id
    assert coverage_refusal.status_code == 409
    error = coverage_refusal.json()["error"]
    assert error["fields"][0]["type"] == SubmissionRefusal.household_has_coverage.value
    assert error["related_case_id"] == covered_case_id


async def test_FR_001_26_applicant_list_never_holds_other_household_case(
    integration_settings: Settings,
) -> None:
    async with screens(integration_settings, worker=False) as s:
        mine = (await submit(s.applicant(ELIGIBLE_HOUSEHOLD))).json()["case_id"]
        theirs = (await submit(s.applicant(INELIGIBLE_HOUSEHOLD))).json()["case_id"]
        listed = await s.applicant(ELIGIBLE_HOUSEHOLD).get(f"{API}/cases")

    assert listed.status_code == 200, listed.text
    ids = [item["case_id"] for item in listed.json()["items"]]
    assert ids == [mine]
    assert theirs not in ids


async def test_FR_001_27_list_is_deadline_ordered_and_filters_by_state(
    integration_settings: Settings,
) -> None:
    async with screens(integration_settings, worker=False) as s:
        case_ids: list[str] = []
        for _ in range(5):
            applicant, _household = s.new_household_applicant()
            case_ids.append((await submit(applicant)).json()["case_id"])
        # Five cases at different ages and states: deadlines day 10, 20, 30, 40 and 50 in reverse
        # creation order, two of them already under review.
        today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        deadlines = {cid: today + timedelta(days=50 - 10 * i) for i, cid in enumerate(case_ids)}
        review = {case_ids[0], case_ids[3]}
        async with mongo(s.settings) as client:
            cases = client[s.settings.MONGO_DB]["cases"]
            for cid, deadline in deadlines.items():
                update: dict[str, Any] = {"decision_deadline": deadline}
                if cid in review:
                    update["state"] = CaseState.under_review.value
                await cases.update_one({"_id": uuid.UUID(cid)}, {"$set": update})
        everything = await s.caseworker.get(f"{API}/cases")
        reviewing = await s.caseworker.get(f"{API}/cases", params={"state": "under_review"})

    assert everything.status_code == 200, everything.text
    ordered = [item["case_id"] for item in everything.json()["items"]]
    assert ordered == sorted(case_ids, key=lambda cid: deadlines[cid])
    in_review = [item["case_id"] for item in reviewing.json()["items"]]
    assert in_review == sorted(review, key=lambda cid: deadlines[cid])
    assert {item["state"] for item in reviewing.json()["items"]} == {"under_review"}


async def test_FR_001_27_applicant_history_403_and_other_household_get_404_audited(
    integration_settings: Settings,
) -> None:
    async with screens(integration_settings, worker=False) as s:
        h1_case = (await submit(s.applicant(ELIGIBLE_HOUSEHOLD))).json()["case_id"]
        h2 = s.applicant(INELIGIBLE_HOUSEHOLD)
        h2_case = (await submit(h2)).json()["case_id"]
        own_history = await h2.get(f"{API}/cases/{h2_case}/history")
        missing = await h2.get(f"{API}/cases/{uuid.uuid4()}")
        other = await h2.get(f"{API}/cases/{h1_case}")
        refused = await s.audit(
            {"action": "access_refused", "reason_code": "wrong_case", "target_type": "case"}
        )

    assert own_history.status_code == 403
    assert other.status_code == 404
    assert other.json()["error"]["code"] == missing.json()["error"]["code"]
    assert other.json()["error"]["message"] == missing.json()["error"]["message"]
    # One audit entry per refused lookup (the missing id and the other household's case look
    # the same to the caller), none holding the case's data.
    assert len(refused) == 2
    assert {e["outcome"] for e in refused} == {"refused"}


async def test_FR_001_16_unseeded_household_case_goes_to_review(
    integration_settings: Settings,
) -> None:
    async with screens(integration_settings, worker=True) as s:
        applicant, household_id = s.new_household_applicant()
        case_id = (await submit(applicant)).json()["case_id"]
        waiting = await wait_for_state(applicant, case_id, CaseState.under_review)
        (case,) = await s.documents("cases", {"household_id": household_id})
        tasks = await s.documents("review_tasks", {"case_id": uuid.UUID(case_id)})

    assert waiting["outcome"] is None
    assert waiting["open_review_task"]["reason"] == "cannot_determine"
    assert (case["state"], case["open"], case.get("outcome")) == ("under_review", True, None)
    assert [(t["status"], t["reason"]) for t in tasks] == [("open", "cannot_determine")]
