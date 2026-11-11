"""Reads for snapshot capture and the snapshot store (FR-003-16, FR-003-19; ADR-003-03).

Everything goes through the worker scope: `household_snapshots` and `applications` by case,
`household_members` by household. A snapshot is inserted once per `(case_id, as_of_date)`; a
duplicate key means an earlier attempt already stored it (TW-11).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol

from pymongo.errors import DuplicateKeyError

from apps.worker.repositories.documents import from_bson, money_to_bson
from packages.domain.case import ApplicationForm
from packages.domain.household import HouseholdMember, HouseholdSnapshot
from packages.platform.mongo import Client
from packages.platform.scope import Scope, find_many, find_one, insert_one

HOUSEHOLD_SNAPSHOTS = "household_snapshots"
HOUSEHOLD_MEMBERS = "household_members"
APPLICATIONS = "applications"
SNAPSHOT_FIELDS = {name: 1 for name in HouseholdSnapshot.model_fields if name != "snapshot_id"}
MEMBER_FIELDS = {name: 1 for name in HouseholdMember.model_fields if name != "member_id"}


class SnapshotSource(Protocol):
    async def snapshot_for(self, scope: Scope, as_of_date: date) -> HouseholdSnapshot | None: ...

    async def members_for_year(self, scope: Scope, year: int) -> list[HouseholdMember]: ...

    async def application_form(self, scope: Scope) -> ApplicationForm | None: ...

    async def insert_snapshot(self, scope: Scope, snapshot: HouseholdSnapshot) -> bool:
        """Store the snapshot. False when one already exists for its case and date."""
        ...


def snapshot_document(snapshot: HouseholdSnapshot) -> dict[str, Any]:
    document: dict[str, Any] = money_to_bson(snapshot.model_dump())
    document["_id"] = document.pop("snapshot_id")
    return document


def snapshot_from_document(document: dict[str, Any]) -> HouseholdSnapshot:
    fields = from_bson({key: value for key, value in document.items() if key != "_id"})
    return HouseholdSnapshot.model_validate({**fields, "snapshot_id": document["_id"]})


class MongoSnapshotSource:
    def __init__(self, client: Client, db_name: str) -> None:
        self._db = client[db_name]

    async def snapshot_for(self, scope: Scope, as_of_date: date) -> HouseholdSnapshot | None:
        document = await find_one(
            self._db,
            HOUSEHOLD_SNAPSHOTS,
            scope,
            {"as_of_date": money_to_bson(as_of_date)},
            projection=SNAPSHOT_FIELDS,
        )
        return snapshot_from_document(document) if document else None

    async def members_for_year(self, scope: Scope, year: int) -> list[HouseholdMember]:
        documents = await find_many(
            self._db, HOUSEHOLD_MEMBERS, scope, {"coverage_year": year}, projection=MEMBER_FIELDS
        )
        return [
            HouseholdMember.model_validate(
                {**from_bson({k: v for k, v in d.items() if k != "_id"}), "member_id": d["_id"]}
            )
            for d in documents
        ]

    async def application_form(self, scope: Scope) -> ApplicationForm | None:
        document = await find_one(self._db, APPLICATIONS, scope, None, projection={"form": 1})
        return ApplicationForm.model_validate(from_bson(document["form"])) if document else None

    async def insert_snapshot(self, scope: Scope, snapshot: HouseholdSnapshot) -> bool:
        try:
            await insert_one(self._db, HOUSEHOLD_SNAPSHOTS, scope, snapshot_document(snapshot))
        except DuplicateKeyError:
            return False
        return True
