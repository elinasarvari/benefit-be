"""The determination store (FR-003-04, FR-003-19; ADR-003-02).

The stored document is `DeterminationRecord` with `_id` = `determination_id`. Insert goes through
the worker scope (by case). A duplicate key means an earlier attempt of the same activity already
stored it, so the insert counts as done (TW-11).
"""

from __future__ import annotations

from typing import Any, Protocol

from pymongo.errors import DuplicateKeyError

from apps.worker.repositories.documents import money_to_bson
from packages.domain.eligibility import DeterminationRecord
from packages.platform.mongo import Client
from packages.platform.scope import Scope, insert_one

DETERMINATIONS = "determinations"


class DeterminationStore(Protocol):
    async def insert(self, scope: Scope, record: DeterminationRecord) -> bool:
        """Store the record. False when it was already stored (a retry)."""
        ...


def determination_document(record: DeterminationRecord) -> dict[str, Any]:
    document: dict[str, Any] = money_to_bson(record.model_dump())
    document["_id"] = document.pop("determination_id")
    return document


class MongoDeterminationStore:
    def __init__(self, client: Client, db_name: str) -> None:
        self._db = client[db_name]

    async def insert(self, scope: Scope, record: DeterminationRecord) -> bool:
        try:
            await insert_one(self._db, DETERMINATIONS, scope, determination_document(record))
        except DuplicateKeyError:
            return False
        return True
