"""The daily schedule that opens due redeterminations (007, FR-007-01, ADR-007-02).

Created at worker start if missing, so a restart or a second worker never duplicates it.
"""

from __future__ import annotations

from datetime import timedelta

import structlog
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleCalendarSpec,
    SchedulePolicy,
    ScheduleRange,
    ScheduleSpec,
)
from temporalio.client import ScheduleOverlapPolicy as Overlap

log = structlog.get_logger()

SCHEDULE_ID = "redeterminations-due"
TIME_ZONE = "America/Chicago"
RUN_HOUR = 6


def _schedule(task_queue: str) -> Schedule:
    return Schedule(
        action=ScheduleActionStartWorkflow(
            "RedeterminationsDueWorkflow",
            id=SCHEDULE_ID,
            task_queue=task_queue,
        ),
        spec=ScheduleSpec(
            calendars=[
                ScheduleCalendarSpec(hour=[ScheduleRange(RUN_HOUR)], minute=[ScheduleRange(0)])
            ],
            time_zone_name=TIME_ZONE,
        ),
        policy=SchedulePolicy(overlap=Overlap.SKIP, catchup_window=timedelta(days=1)),
    )


async def ensure_redeterminations_due_schedule(
    client: Client, task_queue: str, *, enabled: bool = True
) -> bool:
    """Create the schedule if it is missing. True when this call created it."""
    if not enabled:
        log.info("schedule.disabled", schedule_id=SCHEDULE_ID)
        return False
    try:
        await client.create_schedule(SCHEDULE_ID, _schedule(task_queue))
    except ScheduleAlreadyRunningError:
        return False
    log.info("schedule.created", schedule_id=SCHEDULE_ID)
    return True
