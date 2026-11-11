"""Reminder and escalation flags on the case clock (FR-010-02, ADR-010-03, TW-8, TW-13)."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from apps.worker.workflows.calendar import chicago_date
from apps.worker.workflows.review_harness import (
    HarnessScenario,
    ReviewHarnessWorkflow,
)
from apps.worker.workflows.review_ports import AdvanceClock
from packages.domain.review import ActionType, ReviewFlag
from tests.apps.worker.review_support import action_signal
from tests.apps.worker.workflows.review_env import (
    Stubs,
    harness_input,
    review_env,
    start,
    wait_for_open_tasks,
)

MINUTE = timedelta(minutes=1)


async def wait_for_flags(stubs: Stubs, count: int) -> None:
    for _ in range(200):
        if len(stubs.flags) >= count:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"expected {count} flag update(s), got {stubs.flags}")


async def test_FR_010_02_task_open_2_days_marked_reminder() -> None:
    stubs = Stubs()

    async with review_env(stubs) as env:
        handle = await start(env, harness_input(HarnessScenario.determine))
        [opened] = await wait_for_open_tasks(handle)
        await env.sleep(timedelta(days=2) + MINUTE)
        await wait_for_flags(stubs, 1)
        [task] = await wait_for_open_tasks(handle)

    assert [u.flag for u in stubs.flags] == [ReviewFlag.reminder]
    assert stubs.flags[0].task_id == opened.task_id
    assert task.flags == [ReviewFlag.reminder]
    assert task.reminder_at is not None
    assert task.reminder_at - task.opened_at_case >= timedelta(days=2)


async def test_FR_010_02_task_open_4_days_flagged_escalated() -> None:
    stubs = Stubs()

    async with review_env(stubs) as env:
        handle = await start(env, harness_input(HarnessScenario.determine))
        await wait_for_open_tasks(handle)
        await env.sleep(timedelta(days=4) + MINUTE)
        await wait_for_flags(stubs, 2)
        [task] = await wait_for_open_tasks(handle)

    assert [u.flag for u in stubs.flags] == [ReviewFlag.reminder, ReviewFlag.escalated]
    assert task.flags == [ReviewFlag.reminder, ReviewFlag.escalated]
    assert task.escalated_at is not None
    assert task.escalated_at - task.opened_at_case >= timedelta(days=4)


async def test_FR_010_02_coverage_end_passes_redetermination_task_escalated() -> None:
    stubs = Stubs()

    async with review_env(stubs) as env:
        # Coverage ends today (Chicago), so it has passed by the next Chicago midnight (< 1 day).
        coverage_end = chicago_date(await env.get_current_time())
        data = harness_input(HarnessScenario.missed_response_window, coverage_end=coverage_end)
        handle = await start(env, data)
        await wait_for_open_tasks(handle)
        await env.sleep(timedelta(days=1) + MINUTE)
        await wait_for_flags(stubs, 1)
        [task] = await wait_for_open_tasks(handle)

    assert [u.flag for u in stubs.flags] == [ReviewFlag.escalated]
    assert task.flags == [ReviewFlag.escalated]
    assert task.coverage_end == coverage_end


async def test_FR_010_02_action_at_reminder_time_wins_and_no_flag_set() -> None:
    stubs = Stubs()

    async with review_env(stubs) as env:
        handle = await start(env, harness_input(HarnessScenario.determine))
        [task] = await wait_for_open_tasks(handle)
        # Queue the action, then let the reminder become due before the workflow next wakes:
        # queued actions are processed first, so the closed task never gets the flag.
        await handle.signal(
            ReviewHarnessWorkflow.caseworker_action,
            action_signal(task.task_id, ActionType.approve_denial),
        )
        await env.sleep(timedelta(days=2) + MINUTE)
        result = await handle.result()

    assert len(result.results) == 1
    assert stubs.flags == []


async def test_FR_010_02_advance_clock_brings_reminder_forward() -> None:
    stubs = Stubs()

    async with review_env(stubs) as env:
        handle = await start(env, harness_input(HarnessScenario.determine))
        await wait_for_open_tasks(handle)
        before = await env.get_current_time()
        await handle.signal(ReviewHarnessWorkflow.advance_clock, AdvanceClock(days=2))
        await wait_for_flags(stubs, 1)
        after = await env.get_current_time()

    assert [u.flag for u in stubs.flags] == [ReviewFlag.reminder]
    assert after - before < timedelta(days=1)
