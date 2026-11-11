"""The daily schedule starts every due redetermination (FR-007-01, TW-6).

Runs against `make dev` (real Temporal dev server, worker, MongoDB, seeded synthetic data).
Marked `stack` below: it needs the running stack, so the unit run skips it.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from temporalio.service import RPCError

from packages.domain.case import redetermination_case_id, workflow_id_for
from packages.platform.mongo import make_client
from packages.platform.settings import load_settings
from tests.integration.case_stack import connect_temporal

pytestmark = pytest.mark.stack

SCHEDULE_ID = "redeterminations-due"
DUE_WITHIN_DAYS = 60
WAIT_S = 60.0


async def _due_periods() -> list[tuple[UUID, UUID]]:
    settings = load_settings()
    client = make_client(settings.MONGO_URL_OWNER.get_secret_value())
    try:
        db = client[settings.MONGO_DB]
        horizon = datetime.now(UTC) + timedelta(days=DUE_WITHIN_DAYS)
        busy = {doc["household_id"] async for doc in db["cases"].find({"open": True})}
        periods = db["coverage_periods"].find({"status": "active", "end_date": {"$lte": horizon}})
        return [
            (doc["household_id"], doc["_id"])
            async for doc in periods
            if doc["household_id"] not in busy
        ]
    finally:
        await client.close()


async def _trigger_and_collect() -> tuple[list[tuple[UUID, UUID]], list[str]]:
    settings = load_settings()
    due = await _due_periods()
    process = await asyncio.create_subprocess_exec(
        "temporal",
        "schedule",
        "trigger",
        "--schedule-id",
        SCHEDULE_ID,
        "--address",
        settings.TEMPORAL_ADDRESS,
        "--namespace",
        settings.TEMPORAL_NAMESPACE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    assert process.returncode == 0, output.decode()

    temporal = await connect_temporal(settings)
    missing = [str(period) for _, period in due]
    deadline = asyncio.get_running_loop().time() + WAIT_S
    started: list[str] = []
    while missing and asyncio.get_running_loop().time() < deadline:
        started = []
        for household, period in due:
            workflow_id = workflow_id_for(redetermination_case_id(household, period))
            try:
                await temporal.get_workflow_handle(workflow_id).describe()
            except RPCError:
                pass
            else:
                started.append(str(period))
        missing = [str(p) for _, p in due if str(p) not in started]
        if missing:
            await asyncio.sleep(1)
    return due, missing


def test_FR_007_01_triggered_schedule_starts_every_active_period_ending_within_60_days() -> None:
    """FR-007-01: triggered schedule starts every active period ending within 60 days."""
    due, missing = asyncio.run(_trigger_and_collect())

    assert due, "no active coverage period ends within 60 days: run `make reset`"
    assert missing == [], f"the schedule did not start a redetermination for periods {missing}"
