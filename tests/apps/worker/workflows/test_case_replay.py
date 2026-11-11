"""Replay test (TW-14, R9; SC-001-05).

`histories/case_approved.json` and `case_override.json` were recorded from green time-skipping runs
of CaseWorkflow (`record_case_histories.py`). If the command order changes on purpose, record new
histories and keep the old ones as a separate test until no running execution uses them (TW-7).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from temporalio.client import WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer

from apps.worker.workflows.case import CaseWorkflow

HISTORIES = Path(__file__).parents[1] / "histories"


@pytest.mark.parametrize("name", ["case_approved.json", "case_override.json"])
async def test_FR_001_03_case_replay(name: str) -> None:
    history = WorkflowHistory.from_json(name, (HISTORIES / name).read_text())
    replayer = Replayer(workflows=[CaseWorkflow], data_converter=pydantic_data_converter)

    await replayer.replay_workflow(history)


@pytest.mark.parametrize("name", ["case_approved.json", "case_override.json"])
async def test_FR_007_08_application_histories_replay_with_patched_hook(name: str) -> None:
    history = WorkflowHistory.from_json(name, (HISTORIES / name).read_text())
    replayer = Replayer(workflows=[CaseWorkflow], data_converter=pydantic_data_converter)

    await replayer.replay_workflow(history)
