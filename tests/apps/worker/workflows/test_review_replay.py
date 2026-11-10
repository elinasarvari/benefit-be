"""Replay test for the review loop (TW-14).

`histories/review_v1.json` was recorded from a green time-skipping run of ReviewHarnessWorkflow:
an Ineligible determination opens an adverse-determination task, the reminder fires after 2 case
days, then a caseworker approves the denial. Synthetic IDs only (PR-3). If the command order changes
on purpose, record `review_v2.json` and keep this one while executions on it may still run (TW-7).
"""

from __future__ import annotations

import json
from pathlib import Path

from temporalio.client import WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer

from apps.worker.workflows.review_harness import ReviewHarnessWorkflow

HISTORY = Path(__file__).parents[1] / "histories" / "review_v1.json"


def activity_types(raw: str) -> list[str]:
    events = json.loads(raw)["events"]
    return [
        e["activityTaskScheduledEventAttributes"]["activityType"]["name"]
        for e in events
        if "activityTaskScheduledEventAttributes" in e
    ]


async def test_FR_010_01_review_history_replays_without_nondeterminism() -> None:
    raw = HISTORY.read_text()
    history = WorkflowHistory.from_json("review-v1", raw)
    replayer = Replayer(workflows=[ReviewHarnessWorkflow], data_converter=pydantic_data_converter)

    await replayer.replay_workflow(history)

    assert activity_types(raw) == [
        "evaluate",
        "upsert_review_task",
        "set_review_flag",
        "record_caseworker_action",
        "close_review_task",
    ]
