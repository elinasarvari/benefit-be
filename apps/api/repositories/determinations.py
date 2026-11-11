"""Scoped read of `determinations` for the case page (FR-003-23, ADR-003-05).

Goes through `packages.platform.scope`, like every household-scoped read (PL-6). The index
`(case_id, recorded_at desc)` of migration 0005 serves this query.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from packages.domain.eligibility import DeterminationRecord
from packages.platform.mongo import Client
from packages.platform.scope import Scope, scoped_filter

DETERMINATIONS = "determinations"
MAX_DETERMINATIONS = 20
RECORD_FIELDS = {name: 1 for name in DeterminationRecord.model_fields} | {"_id": 1}


def record_from_bson(doc: dict[str, Any]) -> DeterminationRecord:
    """Stored dates are BSON dates at midnight UTC; the model wants calendar dates."""
    determination = dict(doc["determination"])
    if isinstance(determination.get("as_of_date"), datetime):
        determination["as_of_date"] = determination["as_of_date"].astimezone(UTC).date()
    recorded_at = doc["recorded_at"]
    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=UTC)
    return DeterminationRecord.model_validate(
        {
            "determination_id": doc["_id"],
            "household_id": doc["household_id"],
            "case_id": doc["case_id"],
            "snapshot_id": doc["snapshot_id"],
            "recorded_at": recorded_at,
            "determination": determination,
        }
    )


class MongoDeterminationRepository:
    def __init__(self, client: Client, db_name: str) -> None:
        self._client = client
        self._db_name = db_name

    async def list_for_case(self, scope: Scope, case_id: UUID) -> list[DeterminationRecord]:
        """The case's determinations, newest first, at most 20."""
        db = self._client[self._db_name]
        cursor = (
            db[DETERMINATIONS]
            .find(scoped_filter(scope, DETERMINATIONS, {"case_id": case_id}), RECORD_FIELDS)
            .sort([("recorded_at", -1), ("_id", 1)])
            .limit(MAX_DETERMINATIONS)
        )
        return [record_from_bson(doc) for doc in await cursor.to_list(length=MAX_DETERMINATIONS)]
