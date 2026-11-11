"""Scoped read of a case's notices for staff (007a, FR-007-03): projections, no notice text."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from apps.api.schemas.mvp import CaseNoticeView
from packages.platform.mongo import Client
from packages.platform.scope import Scope, scoped_filter

NOTICES = "notices"
NOTICE_FIELDS = {
    "_id": 1,
    "notice_type": 1,
    "status": 1,
    "items": 1,
    "due_case_date": 1,
    "created_at": 1,
    "delivered_at": 1,
}


def _view(doc: dict[str, Any]) -> CaseNoticeView:
    due = doc.get("due_case_date")
    if isinstance(due, datetime):
        due = due.astimezone(UTC).date()
    return CaseNoticeView(
        notice_id=doc["_id"],
        notice_type=doc["notice_type"],
        status=doc["status"],
        items=doc.get("items", []),
        due_case_date=due,
        created_at=doc["created_at"],
        delivered_at=doc.get("delivered_at"),
    )


class MongoNoticeRepository:
    def __init__(self, client: Client, db_name: str) -> None:
        self._db = client[db_name]

    async def list_for_case(self, scope: Scope, case_id: UUID, limit: int) -> list[CaseNoticeView]:
        """Newest first (index `case_created`)."""
        cursor = (
            self._db[NOTICES]
            .find(scoped_filter(scope, NOTICES, {"case_id": case_id}), NOTICE_FIELDS)
            .sort("created_at", -1)
            .limit(limit)
        )
        return [_view(doc) for doc in await cursor.to_list(length=limit)]
