"""`caseworker_actions`: every applied or rejected caseworker action (FR-010-01, -05, -06).

One transaction per action: the action, plus its `eval_overrides` record when it overrides an AI
result (research R6), or its `audit_log` refusal when rejected (R8). `action_id` is also the
document `_id`, so a second insert of the same action is a duplicate key and a no-op (TW-11).

The caseworker's `reason` is stored, never logged (PL-11).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.errors import DuplicateKeyError

from apps.worker.repositories.documents import case_filter, to_bson
from packages.domain.audit import ActorType, AuditAction, AuditLogEntry, AuditOutcome
from packages.domain.review import AI_OVERRIDES, ActionOutcome, CaseworkerAction
from packages.platform.audit import AuditWriter
from packages.platform.mongo import Client

COLLECTION = "caseworker_actions"
EVAL_OVERRIDES = "eval_overrides"
AUDIT_TARGET = "review_task"


def _override_document(action: CaseworkerAction) -> dict[str, Any]:
    corrected = action.corrected_value
    document: dict[str, Any] = to_bson(
        {
            "_id": action.action_id,
            "action_id": action.action_id,
            "household_id": action.household_id,
            "case_id": action.case_id,
            "task_id": action.task_id,
            "action": action.action,
            "finding_id": action.finding_id,
            "field_ref": corrected.field_ref if corrected else None,
            "corrected_value": corrected.value if corrected else None,
            "reviewer_id": action.reviewer_id,
            "recorded_at": action.recorded_at,
        }
    )
    return document


def _refusal(action: CaseworkerAction) -> AuditLogEntry:
    return AuditLogEntry(
        occurred_at=action.recorded_at,
        actor_type=ActorType.user,
        actor_id=str(action.reviewer_id),
        actor_role=action.reviewer_role,
        action=AuditAction.caseworker_action,
        target_type=AUDIT_TARGET,
        target_id=str(action.task_id),
        outcome=AuditOutcome.refused,
        reference_id=str(action.action_id),
    )


class CaseworkerActionRepository:
    def __init__(self, client: Client, db_name: str, audit: AuditWriter) -> None:
        self._client = client
        self._db = client[db_name]
        self._audit = audit

    async def record(self, action: CaseworkerAction) -> CaseworkerAction:
        """Store the action once. Returns what is stored: this action, or the earlier copy."""
        try:
            async with self._client.start_session() as session:
                await session.with_transaction(lambda s: self._write(action, s))
        except DuplicateKeyError:
            existing = await self.get(action.household_id, action.case_id, action.action_id)
            if existing is None:
                raise  # the duplicate was in another collection: nothing was written
            return existing
        return action

    async def _write(self, action: CaseworkerAction, session: AsyncClientSession) -> None:
        document = to_bson(action.model_dump())
        document["_id"] = action.action_id
        await self._db[COLLECTION].insert_one(document, session=session)
        if action.outcome is ActionOutcome.applied and action.action in AI_OVERRIDES:
            await self._db[EVAL_OVERRIDES].insert_one(_override_document(action), session=session)
        if action.outcome is ActionOutcome.rejected:
            await self._audit.write(_refusal(action), session=session)

    async def get(
        self, household_id: UUID, case_id: UUID, action_id: UUID
    ) -> CaseworkerAction | None:
        document = await self._db[COLLECTION].find_one(
            case_filter(COLLECTION, household_id, case_id, {"_id": action_id})
        )
        if document is None:
            return None
        document.pop("_id")
        return CaseworkerAction.model_validate(document)
