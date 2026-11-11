"""Review queue activities: thin calls into the worker repositories (TW-9, TW-11).

Logs carry IDs and codes only, never the caseworker's free-text reason or a corrected value (PL-11).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

import structlog
from temporalio import activity

from apps.worker.review_payloads import ReviewFlagUpdate, ReviewTaskClose
from packages.domain.review import CaseworkerAction, ReviewFlag, ReviewTask

log = structlog.get_logger()


class ReviewTaskStore(Protocol):
    async def upsert(self, task: ReviewTask) -> None: ...

    async def set_flag(
        self, household_id: UUID, case_id: UUID, task_id: UUID, flag: ReviewFlag, at_case: datetime
    ) -> bool: ...

    async def close(
        self, household_id: UUID, case_id: UUID, task_id: UUID, action_id: UUID
    ) -> bool: ...


class CaseworkerActionStore(Protocol):
    async def record(self, action: CaseworkerAction) -> CaseworkerAction: ...


def _context(case_id: UUID) -> dict[str, str]:
    return {"case_id": str(case_id), "workflow_id": activity.info().workflow_id or ""}


class ReviewTaskActivities:
    """Built once per worker process with its repositories injected (TW-12)."""

    def __init__(self, tasks: ReviewTaskStore, actions: CaseworkerActionStore) -> None:
        self._tasks, self._actions = tasks, actions

    @activity.defn(name="upsert_review_task")
    async def upsert_review_task(self, task: ReviewTask) -> None:
        await self._tasks.upsert(task)
        log.info(
            "review_task.upserted",
            task_id=str(task.task_id),
            reason=task.reason.value,
            **_context(task.case_id),
        )

    @activity.defn(name="set_review_flag")
    async def set_review_flag(self, update: ReviewFlagUpdate) -> bool:
        changed = await self._tasks.set_flag(
            update.household_id, update.case_id, update.task_id, update.flag, update.at_case
        )
        log.info(
            "review_task.flagged",
            task_id=str(update.task_id),
            flag=update.flag.value,
            changed=changed,
            **_context(update.case_id),
        )
        return changed

    @activity.defn(name="close_review_task")
    async def close_review_task(self, close: ReviewTaskClose) -> bool:
        changed = await self._tasks.close(
            close.household_id, close.case_id, close.task_id, close.action_id
        )
        log.info(
            "review_task.closed",
            task_id=str(close.task_id),
            action_id=str(close.action_id),
            changed=changed,
            **_context(close.case_id),
        )
        return changed

    @activity.defn(name="record_caseworker_action")
    async def record_caseworker_action(self, action: CaseworkerAction) -> None:
        await self._actions.record(action)
        log.info(
            "caseworker_action.recorded",
            task_id=str(action.task_id),
            action_id=str(action.action_id),
            action=action.action.value,
            outcome=action.outcome.value,
            rejection_code=action.rejection_code.value if action.rejection_code else None,
            **_context(action.case_id),
        )
