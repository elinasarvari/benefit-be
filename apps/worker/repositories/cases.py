"""Worker-side case writes: one `CaseChange` in one transaction (ADR-001-01, KD-10, database.md).

The workflow owns the case state; this module only projects it into MongoDB. Every write is
idempotent: the event is keyed by its workflow-generated `_id` and the unique `(case_id, seq)`
index, so a retried change hits a duplicate key on its first insert and the whole transaction is
dropped as "already applied". Every other document has a deterministic `_id` and is upserted, and
the case is only updated by a newer `seq`. Every filter goes through the worker scope
(ADR-001-06).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from enum import Enum
from typing import Any

from pydantic import BaseModel
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.errors import DuplicateKeyError

from packages.domain.audit import ActorType, AuditAction, AuditLogEntry, AuditOutcome
from packages.domain.case import ActionOutcome, CaseChange
from packages.platform.audit import AuditWriter
from packages.platform.errors import Forbidden
from packages.platform.mongo import Client
from packages.platform.scope import Scope, scoped_filter

CASES = "cases"
CASE_EVENTS = "case_events"
REVIEW_TASKS = "review_tasks"
CASEWORKER_ACTIONS = "caseworker_actions"
COVERAGE_PERIODS = "coverage_periods"
OUTBOX = "outbox"


def to_bson(value: Any) -> Any:
    """Enums as their values, calendar dates as midnight-UTC BSON dates (0002 migration)."""
    if isinstance(value, BaseModel):
        return to_bson(value.model_dump(by_alias=True))
    if isinstance(value, dict):
        return {key: to_bson(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [to_bson(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, time(0), tzinfo=UTC)
    return value


def _check_owned(scope: Scope, collection: str, document: dict[str, Any]) -> None:
    """A document may only be written inside the scope it would be read with."""
    limit = scoped_filter(scope, collection)
    if any(document.get(key) != expected for key, expected in limit.items()):
        raise Forbidden()


def audit_entry_for(change: CaseChange) -> AuditLogEntry | None:
    action = change.action
    if action is None:
        return None
    return AuditLogEntry(
        occurred_at=action.real_time,
        actor_type=ActorType.user,
        actor_id=str(action.reviewer_id),
        action=AuditAction.caseworker_action,
        target_type="case",
        target_id=str(change.case_id),
        outcome=(
            AuditOutcome.success
            if action.outcome is ActionOutcome.applied
            else AuditOutcome.refused
        ),
        reference_id=str(action.id),
    )


class CaseRepository:
    def __init__(self, client: Client, db_name: str, audit: AuditWriter) -> None:
        self._client = client
        self._db = client[db_name]
        self._audit = audit

    async def apply(self, change: CaseChange, scope: Scope) -> bool:
        """Write the change. Returns False when it was already applied (a retry)."""
        if scope.case_id != change.case_id or scope.household_id != change.household_id:
            raise Forbidden()

        async def write(session: AsyncClientSession) -> None:
            await self._write_event(change, scope, session)
            await self._write_case(change, scope, session)
            await self._write_review_task(change, scope, session)
            await self._write_action(change, scope, session)
            await self._write_coverage(change, scope, session)
            await self._write_notice(change, scope, session)
            await self._write_audit(change, session)

        async with self._client.start_session() as session:
            try:
                await session.with_transaction(write)
            except DuplicateKeyError:
                return False
        return True

    async def _upsert(
        self, collection: str, document: dict[str, Any], scope: Scope, session: AsyncClientSession
    ) -> None:
        _check_owned(scope, collection, document)
        await self._db[collection].replace_one(
            scoped_filter(scope, collection, {"_id": document["_id"]}),
            document,
            upsert=True,
            session=session,
        )

    async def _write_event(
        self, change: CaseChange, scope: Scope, session: AsyncClientSession
    ) -> None:
        document = to_bson(change.event)
        _check_owned(scope, CASE_EVENTS, document)
        await self._db[CASE_EVENTS].insert_one(document, session=session)

    async def _write_case(
        self, change: CaseChange, scope: Scope, session: AsyncClientSession
    ) -> None:
        fields: dict[str, Any] = {"last_seq": change.seq, "updated_at": change.event.real_time}
        if change.case_update is not None:
            fields.update(to_bson(change.case_update))
        # Only a newer change moves the case: a late retry of an older one never overwrites it.
        await self._db[CASES].update_one(
            scoped_filter(scope, CASES, {"_id": change.case_id, "last_seq": {"$lt": change.seq}}),
            {"$set": fields},
            session=session,
        )

    async def _write_review_task(
        self, change: CaseChange, scope: Scope, session: AsyncClientSession
    ) -> None:
        if change.review_task is not None:
            await self._upsert(REVIEW_TASKS, to_bson(change.review_task), scope, session)

    async def _write_action(
        self, change: CaseChange, scope: Scope, session: AsyncClientSession
    ) -> None:
        if change.action is not None:
            await self._upsert(CASEWORKER_ACTIONS, to_bson(change.action), scope, session)

    async def _write_coverage(
        self, change: CaseChange, scope: Scope, session: AsyncClientSession
    ) -> None:
        if change.coverage is not None:
            await self._upsert(COVERAGE_PERIODS, to_bson(change.coverage), scope, session)

    async def _write_notice(
        self, change: CaseChange, scope: Scope, session: AsyncClientSession
    ) -> None:
        if change.notice is not None:
            await self._upsert(OUTBOX, to_bson(change.notice), scope, session)

    async def _write_audit(self, change: CaseChange, session: AsyncClientSession) -> None:
        entry = audit_entry_for(change)
        if entry is not None:
            await self._audit.write(entry, session=session)
