"""Replay and restart of the redetermination path (TW-14, TW-16; ADR-007-02).

The histories were recorded by `record_redetermination_histories.py`. If the command order changes
on purpose, record new ones and keep these as a separate test (TW-7).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from temporalio.client import WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer

from apps.worker.workflows.case import CaseWorkflow
from apps.worker.workflows.redeterminations_due import RedeterminationsDueWorkflow
from packages.domain.case import CaseOutcome, CaseState
from tests.apps.worker.workflows.case_harness import ELIGIBLE, environment, wait_for_state
from tests.apps.worker.workflows.redetermination_harness import (
    OUT_OF_DATE,
    RenewalStubs,
    reach_awaiting_applicant,
    response,
    send_response,
    start,
    worker,
)

HISTORIES = Path(__file__).parents[1] / "histories"
NO_CACHE: dict[str, Any] = {"max_cached_workflows": 0}  # a stopped worker can't hold a task


@pytest.mark.parametrize(
    ("name", "workflow"),
    [
        ("redetermination_request_approved.json", CaseWorkflow),
        ("redeterminations_due.json", RedeterminationsDueWorkflow),
    ],
)
async def test_FR_007_02_redetermination_histories_replay(name: str, workflow: type) -> None:
    history = WorkflowHistory.from_json(name, (HISTORIES / name).read_text())
    replayer = Replayer(workflows=[workflow], data_converter=pydantic_data_converter)

    await replayer.replay_workflow(history)


async def test_FR_007_11_restart_during_response_wait_resumes_and_renews() -> None:
    stubs = RenewalStubs(result=ELIGIBLE, items=OUT_OF_DATE)
    async with environment() as env:
        async with worker(env.client, stubs, **NO_CACHE):
            handle, _ = await start(env, stubs)
            notice_id = await reach_awaiting_applicant(handle, stubs)

        async with worker(env.client, stubs, **NO_CACHE):
            resumed = await wait_for_state(handle, CaseState.awaiting_applicant)
            await send_response(handle, response(notice_id))
            final = await asyncio.wait_for(handle.result(), timeout=30)

    assert resumed.state is CaseState.awaiting_applicant
    assert final.outcome is CaseOutcome.renewed_after_response
    assert len(stubs.deliveries) == 1  # the approval's effect ran once
    assert len(stubs.drafts) == 1
