"""Replay test (TW-14).

`histories/smoke_v1.json` was recorded from a green time-skipping run of SmokeWorkflow. If the
workflow's command order changes on purpose, record a new history and keep the old one as a
separate test until no running execution uses it (TW-7).
"""

from __future__ import annotations

from pathlib import Path

from temporalio.client import WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer

from apps.worker.workflows.smoke import SmokeWorkflow

HISTORY = Path(__file__).parents[1] / "histories" / "smoke_v1.json"


async def test_smoke_workflow_replays_recorded_history() -> None:
    history = WorkflowHistory.from_json("smoke-v1", HISTORY.read_text())
    replayer = Replayer(workflows=[SmokeWorkflow], data_converter=pydantic_data_converter)

    await replayer.replay_workflow(history)
