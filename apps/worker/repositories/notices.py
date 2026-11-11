"""Worker-side writes of `notices` for a redetermination's request (007, FR-007-03, FR-007-11).

The request is held `pending_approval` when it is prepared and `delivered` when a caseworker
approves it. Both writes are idempotent (TW-11) and go through the worker scope (ADR-001-06).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from apps.worker.repositories.cases import to_bson
from packages.domain.case import NoticeType
from packages.domain.notice import Notice, NoticeStatus, RequestedItem
from packages.platform.mongo import Client
from packages.platform.scope import Scope, scoped_filter

NOTICES = "notices"
# The fixed template text (FR-011-06): the applicant's items, not free text, carry the detail.
REQUEST_TEXT = "We need to confirm some information to renew your coverage."


class MongoNoticeStore:
    def __init__(self, client: Client, db_name: str) -> None:
        self._db = client[db_name]

    async def hold_request(
        self,
        scope: Scope,
        notice_id: UUID,
        items: list[RequestedItem],
        created_at: datetime,
    ) -> None:
        """Insert the request once; a retry finds it and changes nothing."""
        if scope.case_id is None or scope.household_id is None:
            raise ValueError("a worker scope is required")
        notice = Notice(
            id=notice_id,
            case_id=scope.case_id,
            household_id=scope.household_id,
            notice_type=NoticeType.request_for_information,
            status=NoticeStatus.pending_approval,
            text=REQUEST_TEXT,
            items=items,
            created_at=created_at,
            synthetic=True,
        )
        document: dict[str, Any] = to_bson(notice.model_dump(by_alias=True))
        await self._db[NOTICES].update_one(
            scoped_filter(scope, NOTICES, {"_id": notice_id}),
            {"$setOnInsert": document},
            upsert=True,
        )

    async def deliver(
        self, scope: Scope, notice_id: UUID, due_case_date: date, delivered_at: datetime
    ) -> bool:
        """Deliver a held request. False when no such request is held for this case."""
        result = await self._db[NOTICES].update_one(
            scoped_filter(scope, NOTICES, {"_id": notice_id, "status": "pending_approval"}),
            {
                "$set": {
                    "status": NoticeStatus.delivered.value,
                    "due_case_date": to_bson(due_case_date),
                    "delivered_at": delivered_at,
                }
            },
        )
        if result.matched_count:
            return True
        # Already delivered by an earlier attempt of this activity (TW-11).
        done = await self._db[NOTICES].count_documents(
            scoped_filter(scope, NOTICES, {"_id": notice_id, "status": "delivered"}), limit=1
        )
        return done > 0
