"""Reference lookup (FR-002-22): identity and per-job income from the synthetic data set.

Contract stub (MVP contracts PR): `StubReferenceRecords` answers "match, no jobs" for everyone.
002 adds the MongoDB implementation over `reference_records` (seed task S-01), read through the
worker scope like `verified_facts.py`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Protocol

from packages.domain.verification import (
    IdentityMatch,
    ReferenceAnswer,
    ReferenceJob,
    ReferenceLookup,
)
from packages.platform.mongo import Client
from packages.platform.scope import Scope, find_one

REFERENCE_RECORDS = "reference_records"
FIELDS = {"given_name": 1, "family_name": 1, "date_of_birth": 1, "jobs": 1}


class ReferenceRecords(Protocol):
    async def lookup(self, scope: Scope, request: ReferenceLookup) -> ReferenceAnswer: ...


class StubReferenceRecords:
    async def lookup(self, scope: Scope, request: ReferenceLookup) -> ReferenceAnswer:
        return ReferenceAnswer(member_id=request.member_id, identity=IdentityMatch.match)


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC).date()
    return value if isinstance(value, date) else None


class MongoReferenceRecords:
    """The synthetic reference data set (FR-007-13, ADR-007-05): identity matches on the exact
    given name, family name (case-folded) and date of birth; a member with no record has none."""

    def __init__(self, client: Client, db_name: str) -> None:
        self._db = client[db_name]

    async def lookup(self, scope: Scope, request: ReferenceLookup) -> ReferenceAnswer:
        record = await find_one(
            self._db,
            REFERENCE_RECORDS,
            scope,
            {"household_id": request.household_id, "member_id": request.member_id},
            projection=FIELDS,
        )
        if record is None:
            return ReferenceAnswer(member_id=request.member_id, identity=IdentityMatch.no_record)
        same = (
            str(record.get("given_name", "")).casefold() == request.given_name.casefold()
            and str(record.get("family_name", "")).casefold() == request.family_name.casefold()
            and _as_date(record.get("date_of_birth")) == request.date_of_birth
        )
        if not same:
            return ReferenceAnswer(member_id=request.member_id, identity=IdentityMatch.no_match)
        jobs = [
            ReferenceJob(employer=job["employer"], monthly_gross=Decimal(str(job["monthly_gross"])))
            for job in record.get("jobs", [])
        ]
        return ReferenceAnswer(member_id=request.member_id, identity=IdentityMatch.match, jobs=jobs)
