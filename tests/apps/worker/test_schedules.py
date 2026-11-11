"""The daily redeterminations schedule (test-plan TC-37)."""

from __future__ import annotations

from temporalio.client import ScheduleOverlapPolicy
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment

from apps.worker.schedules import SCHEDULE_ID, ensure_redeterminations_due_schedule


async def test_FR_007_01_ensure_schedule_creates_daily_skip_overlap_schedule_once() -> None:
    async with await WorkflowEnvironment.start_local(data_converter=pydantic_data_converter) as env:
        assert await ensure_redeterminations_due_schedule(env.client, "q") is True
        assert await ensure_redeterminations_due_schedule(env.client, "q") is False
        description = await env.client.get_schedule_handle(SCHEDULE_ID).describe()
        spec = description.schedule.spec
        assert SCHEDULE_ID == "redeterminations-due"
        assert spec.time_zone_name == "America/Chicago"
        assert spec.calendars[0].hour[0].start == 6
        assert description.schedule.policy.overlap is ScheduleOverlapPolicy.SKIP

        assert await ensure_redeterminations_due_schedule(env.client, "q2", enabled=False) is False
        ids = [s.id async for s in await env.client.list_schedules()]
        assert ids == [SCHEDULE_ID]
