"""No seeded household's coverage ends automatically (SC-007-03, FR-007-04; TC-62).

Real API app, the real worker process (`apps.worker.main`, with the renewal activities and the
seeded reference records) and Temporal dev server, over seeded households E and F.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from db.seeds.generate import GENERATED_DIR
from packages.domain.case import CaseState
from packages.platform.settings import Settings
from tests.integration.case_stack import (
    API,
    CASEWORKER,
    act,
    api,
    connect_temporal,
    eventually,
    get_case,
    mongo,
    new_task_queue,
    seeded_user,
    stack_settings,
    worker_process,
)
from tests.integration.fixtures import rebuild


def _due_by_path() -> dict[str, dict[str, Any]]:
    path = GENERATED_DIR / "testsets" / "redeterminations_due.json"
    raw = json.loads(path.read_text())
    items: list[dict[str, Any]] = raw["items"] if isinstance(raw, dict) else raw
    by_path: dict[str, dict[str, Any]] = {}
    for item in items:
        by_path.setdefault(item["expected_path"], item)
    return by_path


async def _start(caseworker: httpx.AsyncClient, item: dict[str, Any]) -> str:
    response = await caseworker.post(
        f"{API}/households/{item['household_id']}/redeterminations",
        json={"coverage_period_id": item["coverage_period_id"]},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["case_id"])


async def test_FR_007_04_no_seeded_household_coverage_ends_automatically(
    integration_settings: Settings,
) -> None:
    await rebuild(integration_settings)
    settings = stack_settings(integration_settings, new_task_queue("renewal"), demo_profile=False)
    due = _due_by_path()
    assert set(due) == {"renew_automatically", "ask_applicant"}
    await connect_temporal(settings)

    async with worker_process(settings, module="apps.worker.main"), api(settings) as stack:
        caseworker = stack.client_for(seeded_user(CASEWORKER))
        automatic = await _start(caseworker, due["renew_automatically"])
        asking = await _start(caseworker, due["ask_applicant"])

        async def decided() -> dict[str, Any] | None:
            case = await get_case(caseworker, automatic)
            return case if case["state"] == CaseState.decided.value else None

        renewed = await eventually(decided, "household E case decided")

        async def approval_task() -> dict[str, Any] | None:
            case = await get_case(caseworker, asking)
            task = case["open_review_task"]
            return task if task and "approve" in task["allowed_actions"] else None

        task = await eventually(approval_task, "household F request awaiting approval")
        response = await act(caseworker, asking, task["task_id"], "approve")
        assert response.status_code == 202, response.text

        async def waiting() -> dict[str, Any] | None:
            case = await get_case(caseworker, asking)
            return case if case["state"] == "awaiting_applicant" else None

        await eventually(waiting, "household F waiting for the applicant")

        async with mongo(settings) as client:
            ended = [
                doc
                async for doc in client[settings.MONGO_DB]["coverage_periods"].find(
                    {"status": "ended"}
                )
            ]

    assert renewed["outcome"] == "renewed_automatically"
    assert ended == []
