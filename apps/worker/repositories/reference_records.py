"""Reference lookup (FR-002-22): identity and per-job income from the synthetic data set.

`MongoReferenceRecords` reads `reference_records` (seed task S-01) through the worker scope, which
filters on the scope's `household_id` like `verified_facts.py`. `StubReferenceRecords` stays for
tests that don't need a database.
"""

from __future__ import annotations

from typing import Protocol

from apps.worker.repositories.documents import as_date, from_bson
from packages.domain.verification import (
    IdentityMatch,
    ReferenceAnswer,
    ReferenceJob,
    ReferenceLookup,
)
from packages.platform.mongo import Client
from packages.platform.scope import Scope, find_one

REFERENCE_RECORDS = "reference_records"
FIELDS = {"member_id": 1, "given_name": 1, "family_name": 1, "date_of_birth": 1, "jobs": 1}


class ReferenceRecords(Protocol):
    async def lookup(self, scope: Scope, request: ReferenceLookup) -> ReferenceAnswer: ...


class StubReferenceRecords:
    async def lookup(self, scope: Scope, request: ReferenceLookup) -> ReferenceAnswer:
        return ReferenceAnswer(member_id=request.member_id, identity=IdentityMatch.match)


class MongoReferenceRecords:
    def __init__(self, client: Client, db_name: str) -> None:
        self._db = client[db_name]

    async def lookup(self, scope: Scope, request: ReferenceLookup) -> ReferenceAnswer:
        """`no_record` when the member has no record; `no_match` when the record's name or date of
        birth differs (exact on case-folded names); otherwise `match` with the record's jobs."""
        document = await find_one(
            self._db,
            REFERENCE_RECORDS,
            scope,
            {"household_id": request.household_id, "member_id": request.member_id},
            projection={"_id": 0, **FIELDS},
        )
        if document is None:
            return ReferenceAnswer(member_id=request.member_id, identity=IdentityMatch.no_record)
        same_person = (
            str(document["given_name"]).casefold() == request.given_name.casefold()
            and str(document["family_name"]).casefold() == request.family_name.casefold()
            and as_date(document["date_of_birth"]) == request.date_of_birth
        )
        if not same_person:
            return ReferenceAnswer(member_id=request.member_id, identity=IdentityMatch.no_match)
        jobs = [ReferenceJob.model_validate(from_bson(job)) for job in document.get("jobs", [])]
        return ReferenceAnswer(member_id=request.member_id, identity=IdentityMatch.match, jobs=jobs)
