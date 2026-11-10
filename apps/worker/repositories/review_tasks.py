"""`review_tasks`: the queue's projection of tasks the case workflow owns (ADR-010-01).

Only the workflow, through activities, writes here. Writes are idempotent (TW-11): opening inserts
once, and flags and closing change only tasks still open.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pymongo.asynchronous.database import AsyncDatabase

from apps.worker.repositories.documents import as_date, case_filter, to_bson
from packages.domain.review import ReviewFlag, ReviewTask, ReviewTaskStatus
from packages.platform.clock import Clock

COLLECTION = "review_tasks"
_OPEN = {"status": ReviewTaskStatus.open.value}


class ReviewTaskRepository:
    def __init__(self, db: AsyncDatabase[dict[str, Any]], clock: Clock) -> None:
        self._collection = db[COLLECTION]
        self._clock = clock

    async def upsert(self, task: ReviewTask) -> None:
        """Insert by `_id` once. A retry, or a replayed open, leaves the stored task as it is."""
        document = to_bson(task.model_dump(exclude={"task_id"}))
        await self._collection.update_one(
            case_filter(COLLECTION, task.household_id, task.case_id, {"_id": task.task_id}),
            {"$setOnInsert": document},
            upsert=True,
        )

    async def set_flag(
        self, household_id: UUID, case_id: UUID, task_id: UUID, flag: ReviewFlag, at_case: datetime
    ) -> bool:
        stamp = "reminder_at" if flag is ReviewFlag.reminder else "escalated_at"
        result = await self._collection.update_one(
            case_filter(COLLECTION, household_id, case_id, {"_id": task_id, **_OPEN}),
            {
                "$addToSet": {"flags": flag.value},
                "$set": {stamp: at_case, "updated_at": self._clock.now()},
            },
        )
        return result.modified_count == 1

    async def close(
        self, household_id: UUID, case_id: UUID, task_id: UUID, action_id: UUID
    ) -> bool:
        result = await self._collection.update_one(
            case_filter(COLLECTION, household_id, case_id, {"_id": task_id, **_OPEN}),
            {
                "$set": {
                    "status": ReviewTaskStatus.closed.value,
                    "closed_by_action_id": action_id,
                    "updated_at": self._clock.now(),
                }
            },
        )
        return result.modified_count == 1

    async def get(self, household_id: UUID, case_id: UUID, task_id: UUID) -> ReviewTask | None:
        document = await self._collection.find_one(
            case_filter(COLLECTION, household_id, case_id, {"_id": task_id})
        )
        if document is None:
            return None
        document["task_id"] = document.pop("_id")
        for name in ("decision_deadline", "coverage_end"):
            document[name] = as_date(document.get(name))
        return ReviewTask.model_validate(document)
