"""Worker reads of `coverage_periods` for redeterminations (007, FR-007-01, FR-007-05).

`find_period` is scoped to the case's household. `due_periods` is the one cross-household read of
the schedule (FR-007-01): the daily scan has no household yet. It returns only periods and
nothing else of a household, and every write after it is scoped to one household.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID

from packages.domain.case import CoveragePeriod, CoverageStatus
from packages.platform.mongo import Client
from packages.platform.scope import Scope, find_one

COVERAGE_PERIODS = "coverage_periods"
COVERAGE_FIELDS = {name: 1 for name in CoveragePeriod.model_fields if name != "id"} | {"_id": 1}


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time(0), tzinfo=UTC)


def _period(doc: dict[str, Any]) -> CoveragePeriod:
    for name in ("start_date", "end_date"):
        if isinstance(doc.get(name), datetime):
            doc[name] = doc[name].astimezone(UTC).date()
    return CoveragePeriod.model_validate(doc)


class MongoCoveragePeriods:
    def __init__(self, client: Client, db_name: str) -> None:
        self._db = client[db_name]

    async def find_period(self, scope: Scope, period_id: UUID) -> CoveragePeriod | None:
        doc = await find_one(
            self._db, COVERAGE_PERIODS, scope, {"_id": period_id}, projection=COVERAGE_FIELDS
        )
        return _period(doc) if doc else None

    async def due_periods(self, as_of: date, lead_days: int, limit: int) -> list[CoveragePeriod]:
        """Active periods ending from `as_of` to `as_of + lead_days`, soonest end first."""
        window = {"$gte": _midnight(as_of), "$lte": _midnight(as_of + timedelta(days=lead_days))}
        cursor = (
            self._db[COVERAGE_PERIODS]
            .find({"status": CoverageStatus.active.value, "end_date": window}, COVERAGE_FIELDS)
            .sort([("end_date", 1), ("_id", 1)])
            .limit(limit)
        )
        return [_period(doc) for doc in await cursor.to_list(length=limit)]


class MongoCaseDates:
    """The date a case was received, for activities that must read the same year of held data."""

    def __init__(self, client: Client, db_name: str) -> None:
        self._db = client[db_name]

    async def received_on(self, scope: Scope, case_id: UUID) -> date | None:
        doc = await find_one(
            self._db, "cases", scope, {"_id": case_id}, projection={"received_on": 1}
        )
        if doc is None:
            return None
        value = doc["received_on"]
        return value.astimezone(UTC).date() if isinstance(value, datetime) else value
