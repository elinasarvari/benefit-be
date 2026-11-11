"""`open_due_redeterminations`: the daily scan's step (007, FR-007-01, FR-007-07, ADR-007-06).

Opens a case for every active period ending within `REDETERMINATION_LEAD_DAYS` (at most 200 a
run, soonest end first) and returns what to start: every case it created and every one still open.
A household with another open case is skipped and met again the next day. Idempotent: the case ID
comes from the household and period (TW-11).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol

import structlog
from temporalio import activity

from apps.worker.policies import warn_if_retrying
from apps.worker.repositories.due_cases import Opened, OpenResult
from packages.domain.case import CoveragePeriod
from packages.domain.renewal import (
    REDETERMINATION_LEAD_DAYS,
    DueRedeterminations,
    DueRedeterminationsRequest,
)
from packages.platform.clock import Clock

log = structlog.get_logger()

MAX_PER_RUN = 200
DEMO_SCALE = 1440  # one real minute = one case day (ADR-001-03), as the API's start


class DuePeriods(Protocol):
    async def due_periods(
        self, as_of: date, lead_days: int, limit: int
    ) -> list[CoveragePeriod]: ...


class DueCaseWriter(Protocol):
    async def open_case(self, period: CoveragePeriod, now: datetime, scale: int) -> OpenResult: ...


class DueRedeterminationActivities:
    def __init__(
        self, periods: DuePeriods, cases: DueCaseWriter, clock: Clock, demo_profile: bool
    ) -> None:
        self._periods = periods
        self._cases = cases
        self._clock = clock
        self._scale = DEMO_SCALE if demo_profile else 1

    @activity.defn(name="open_due_redeterminations")
    async def open_due_redeterminations(
        self, request: DueRedeterminationsRequest
    ) -> DueRedeterminations:
        warn_if_retrying()
        periods = await self._periods.due_periods(
            request.as_of_date, REDETERMINATION_LEAD_DAYS, MAX_PER_RUN
        )
        starts = []
        skipped = 0
        for period in periods:
            result = await self._cases.open_case(period, self._clock.now(), self._scale)
            if result.opened is Opened.skipped:
                skipped += 1
            elif result.start is not None:
                starts.append(result.start)
        log.info(
            "redeterminations.due",
            workflow_id=activity.info().workflow_id,
            due=len(periods),
            started=len(starts),
            skipped=skipped,
        )
        return DueRedeterminations(starts=starts)
