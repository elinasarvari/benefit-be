"""Scoped reads for the household page (007a, FR-007-18): members, coverage periods and cases.

Every read goes through `packages.platform.scope` and filters by the household in the path.
Members come back as projections: names and birth date only, never the attested facts.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from apps.api.repositories.cases import (
    CASE_FIELDS,
    CASES,
    COVERAGE_FIELDS,
    COVERAGE_PERIODS,
    _case_from_bson,
    _coverage_from_bson,
)
from apps.api.schemas.mvp import HouseholdMemberView
from packages.domain.case import Case, CoveragePeriod
from packages.platform.mongo import Client
from packages.platform.scope import Scope, find_many, find_one, scoped_filter

HOUSEHOLD_MEMBERS = "household_members"
MEMBER_FIELDS = {
    "_id": 1,
    "given_name": 1,
    "family_name": 1,
    "date_of_birth": 1,
    "relationship": 1,
    "coverage_year": 1,
}
MAX_MEMBERS = 50
MAX_PERIODS = 50
MAX_CASES = 100


def _day(value: Any) -> date:
    return value.astimezone(UTC).date() if isinstance(value, datetime) else value


class MongoHouseholdRepository:
    def __init__(self, client: Client, db_name: str) -> None:
        self._db = client[db_name]

    async def members_of(self, scope: Scope, household_id: UUID) -> list[HouseholdMemberView]:
        """The latest coverage year's members: the seeds store one document set per year."""
        docs = await find_many(
            self._db,
            HOUSEHOLD_MEMBERS,
            scope,
            {"household_id": household_id},
            projection=MEMBER_FIELDS,
            limit=MAX_MEMBERS * 4,
        )
        year = max((doc.get("coverage_year", 0) for doc in docs), default=0)
        return [
            HouseholdMemberView(
                member_id=doc["_id"],
                full_name=f"{doc['given_name']} {doc['family_name']}",
                date_of_birth=_day(doc["date_of_birth"]),
                relationship=doc["relationship"],
            )
            for doc in docs
            if doc.get("coverage_year", 0) == year
        ][:MAX_MEMBERS]

    async def coverage_periods_of(self, scope: Scope, household_id: UUID) -> list[CoveragePeriod]:
        cursor = (
            self._db[COVERAGE_PERIODS]
            .find(
                scoped_filter(scope, COVERAGE_PERIODS, {"household_id": household_id}),
                COVERAGE_FIELDS,
            )
            .sort("end_date", -1)
            .limit(MAX_PERIODS)
        )
        return [_coverage_from_bson(doc) for doc in await cursor.to_list(length=MAX_PERIODS)]

    async def find_period(
        self, scope: Scope, household_id: UUID, period_id: UUID
    ) -> CoveragePeriod | None:
        doc = await find_one(
            self._db,
            COVERAGE_PERIODS,
            scope,
            {"_id": period_id, "household_id": household_id},
            projection=COVERAGE_FIELDS,
        )
        return _coverage_from_bson(doc) if doc else None

    async def cases_of(self, scope: Scope, household_id: UUID) -> list[Case]:
        cursor = (
            self._db[CASES]
            .find(scoped_filter(scope, CASES, {"household_id": household_id}), CASE_FIELDS)
            .sort([("received_on", -1), ("_id", 1)])
            .limit(MAX_CASES)
        )
        return [_case_from_bson(doc) for doc in await cursor.to_list(length=MAX_CASES)]
