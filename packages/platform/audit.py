"""The append-only audit log writer (FR-000-23, KD-12).

The runtime database user can only insert into and read `audit_log`, so this writer only ever calls
`insert_one`. When an entry belongs with another write, pass that write's transaction session.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Protocol

from pymongo.asynchronous.client_session import AsyncClientSession

from packages.domain.audit import AuditLogEntry
from packages.platform.mongo import Client

AUDIT_COLLECTION = "audit_log"


class AuditWriter(Protocol):
    async def write(
        self, entry: AuditLogEntry, session: AsyncClientSession | None = None
    ) -> None: ...


def to_document(entry: AuditLogEntry) -> dict[str, Any]:
    return {
        key: (value.value if isinstance(value, Enum) else value)
        for key, value in entry.model_dump().items()
        if value is not None
    }


class MongoAuditWriter:
    def __init__(self, client: Client, audit_db: str) -> None:
        self._collection = client[audit_db][AUDIT_COLLECTION]

    async def write(self, entry: AuditLogEntry, session: AsyncClientSession | None = None) -> None:
        await self._collection.insert_one(to_document(entry), session=session)


class InMemoryAuditWriter:
    def __init__(self) -> None:
        self.entries: list[AuditLogEntry] = []

    async def write(self, entry: AuditLogEntry, session: AsyncClientSession | None = None) -> None:
        self.entries.append(entry)
