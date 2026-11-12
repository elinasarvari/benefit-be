"""Pay stub upload to check result, on the real stack (002; TC-72, TC-73; ADR-002-01, ADR-002-06).

Real API app, replica-set MongoDB, Temporal dev server and the worker's production activities
(`build_activities`). Upload comes before submit, against `case_id_for(reference)` (ADR-002-01).
The case workflow does not run the verification branch until 011 wires `case.py` (ADR-002-06), so
TC-73 drives the branch through a small test workflow (`verification_probe.py`) that calls the
real `verify_documents` activity and the branch's pure helpers. Household A is seeded, so its
stubs name its stored member and job (ADR-002-04). All data is synthetic (PR-3).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import httpx
from temporalio.client import Client
from temporalio.worker import Worker

from apps.worker.main import build_activities
from apps.worker.workflows.verification_branch import NextStep
from packages.domain.case import case_id_for
from packages.domain.review import ReviewCause
from packages.domain.verification import VerificationRequest
from packages.platform.clock import SystemClock
from packages.platform.mongo import make_client
from packages.platform.settings import Settings
from tests.apps.api.forms import body_dict
from tests.integration.case_stack import (
    API,
    CASEWORKER,
    ELIGIBLE_HOUSEHOLD,
    api,
    applicant_for,
    connect_temporal,
    eventually,
    mongo,
    new_task_queue,
    seeded_user,
    stack_settings,
)
from tests.integration.fixtures import rebuild
from tests.integration.verification_probe import VerificationBranchProbe

PDF_BYTES = b"%PDF-1.4\nSYNTH pay stub\n%%EOF"
TASK_QUEUE_PREFIX = "verify-e2e"


async def seeded_job(settings: Settings, household: uuid.UUID) -> dict[str, Any]:
    """The applicant's stored member and declared job for this year (household A has one).

    For a seeded household the check reads the stored members, as the determination does
    (ADR-002-04), so the stub must name that member and job, not the application form's.
    """
    async with mongo(settings) as client:
        member = await client[settings.MONGO_DB]["household_members"].find_one(
            {"household_id": household, "coverage_year": date.today().year, "relationship": "self"}
        )
    assert member is not None and member["jobs"], "household A has a self member with a job"
    job = member["jobs"][0]
    return {
        "employee_name": f"{member['given_name']} {member['family_name']}",
        "employer": job["employer"],
        "frequency": job["declared_frequency"],
        "declared_gross": Decimal(str(job["declared_gross"])),
    }


def stub_body(job: dict[str, Any], gross_pay: Decimal) -> dict[str, Any]:
    return {
        "employee_name": job["employee_name"],
        "employer": job["employer"],
        "frequency": job["frequency"],
        "gross_pay": str(gross_pay.quantize(Decimal("0.01"))),
        "pay_date": (date.today() - timedelta(days=10)).isoformat(),
        "content_type": "application/pdf",
        "size_bytes": len(PDF_BYTES),
    }


async def upload_stub(
    client: httpx.AsyncClient, case_id: uuid.UUID, job: dict[str, Any], gross_pay: Decimal
) -> str:
    created = await client.post(f"{API}/cases/{case_id}/documents", json=stub_body(job, gross_pay))
    assert created.status_code == 201, created.text
    document_id: str = created.json()["document_id"]
    stored = await client.put(
        f"{API}/cases/{case_id}/documents/{document_id}/content",
        content=PDF_BYTES,
        headers={"content-type": "application/pdf"},
    )
    assert stored.status_code == 204, stored.text
    return document_id


@asynccontextmanager
async def production_worker(
    settings: Settings, temporal: Client, workflows: list[type]
) -> AsyncIterator[None]:
    client = make_client(settings.MONGO_URL_APP.get_secret_value())
    try:
        async with Worker(
            temporal,
            task_queue=settings.TEMPORAL_TASK_QUEUE,
            workflows=workflows,
            activities=build_activities(settings, SystemClock(), client),
        ):
            yield
    finally:
        await client.close()


async def test_FR_002_17_upload_submit_check_end_to_end(integration_settings: Settings) -> None:
    await rebuild(integration_settings)
    settings = stack_settings(
        integration_settings, new_task_queue(TASK_QUEUE_PREFIX), demo_profile=False
    )
    temporal = await connect_temporal(settings)
    reference = uuid.uuid4()
    case_id = case_id_for(reference)

    from apps.worker.workflows.case import CaseWorkflow

    async with api(settings) as stack:
        applicant = stack.client_for(applicant_for(ELIGIBLE_HOUSEHOLD))
        caseworker = stack.client_for(seeded_user(CASEWORKER))
        # The stored job's own declared gross per pay period: documented income equals declared.
        job = await seeded_job(settings, ELIGIBLE_HOUSEHOLD)
        document_id = await upload_stub(applicant, case_id, job, job["declared_gross"])
        submitted = await applicant.post(f"{API}/applications", json=body_dict(reference))
        assert submitted.status_code == 201, submitted.text
        assert submitted.json()["case_id"] == str(case_id)

        async with production_worker(settings, temporal, [CaseWorkflow]):

            async def probe() -> dict[str, Any] | None:
                response = await caseworker.get(f"{API}/cases/{case_id}/verification")
                assert response.status_code == 200, response.text
                body: dict[str, Any] = response.json()
                return body if body["result"] is not None else None

            verification = await eventually(probe, "the check result")

    listed = [d["document_id"] for d in verification["documents"]]
    assert listed == [document_id]
    details = verification["documents"][0]["details"]
    assert details["employer"] == job["employer"]
    assert Decimal(details["gross_pay"]) == job["declared_gross"].quantize(Decimal("0.01"))
    result = verification["result"]
    assert result["complete"] is True
    assert result["missing_items"] == []
    assert [f for f in result["findings"] if f["cause"] == "income_mismatch"] == []


async def test_FR_002_16_mismatch_household_routes_end_to_end(
    integration_settings: Settings,
) -> None:
    await rebuild(integration_settings)
    settings = stack_settings(
        integration_settings, new_task_queue(TASK_QUEUE_PREFIX), demo_profile=False
    )
    temporal = await connect_temporal(settings)
    reference = uuid.uuid4()
    case_id = case_id_for(reference)
    household = ELIGIBLE_HOUSEHOLD

    async with api(settings) as stack:
        applicant = stack.client_for(applicant_for(household))
        # 20% above the stored job's declared gross per period: over the 10% threshold.
        job = await seeded_job(settings, household)
        await upload_stub(applicant, case_id, job, job["declared_gross"] * Decimal("1.2"))
        submitted = await applicant.post(f"{API}/applications", json=body_dict(reference))
        assert submitted.status_code == 201, submitted.text

        request = VerificationRequest(
            case_id=case_id, household_id=household, as_of_date=date.today()
        )
        handle = await temporal.start_workflow(
            VerificationBranchProbe.run,
            request,
            id=f"probe-{case_id}",
            task_queue=settings.TEMPORAL_TASK_QUEUE,
        )
        async with production_worker(settings, temporal, [VerificationBranchProbe]):

            async def routed() -> list[str] | None:
                progress: list[str] = await handle.query("progress")
                return progress if any(p.startswith("cause:") for p in progress) else None

            progress = await eventually(routed, "the verification review to be raised")
            await asyncio.sleep(0.5)  # the determination must not run while the review is open
            assert "determined" not in await handle.query("progress")
            assert progress == [
                f"step:{NextStep.verification_review.value}",
                f"cause:{ReviewCause.income_mismatch.value}",
            ]

            async with mongo(settings) as client:
                stored = await client[settings.MONGO_DB]["verification_results"].find_one(
                    {"case_id": case_id}
                )
            assert stored is not None
            mismatches = [f for f in stored["findings"] if f["cause"] == "income_mismatch"]
            assert len(mismatches) == 1
            assert mismatches[0].get("dismissed") is None

            await handle.signal("review_done")
            assert (await handle.result())[-1] == "determined"
