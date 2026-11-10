"""Scoped reads of `cases`, `coverage_periods`, `review_tasks` and the guarded case insert.

Every read goes through `packages.platform.scope`, so an applicant only ever sees their own
household (R16, PL-6). The insert relies on the indexes of migration 0002 (ADR-001-02):
unique `_id` / `submission_reference`, and one open case per household (partial unique index).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, time
from enum import Enum
from typing import Any, TypeVar
from uuid import UUID

from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.errors import DuplicateKeyError

from packages.domain.audit import AuditLogEntry
from packages.domain.case import (
    Case,
    CaseworkerAction,
    CaseworkerActionType,
    OpenReviewTask,
    ReviewTaskStatus,
)
from packages.platform.audit import AuditWriter
from packages.platform.errors import Forbidden
from packages.platform.mongo import Client
from packages.platform.scope import Scope, find_one, insert_one, scoped_filter

T = TypeVar("T")

CASES = "cases"
COVERAGE_PERIODS = "coverage_periods"
REVIEW_TASKS = "review_tasks"
CASEWORKER_ACTIONS = "caseworker_actions"

CASE_FIELDS = {name: 1 for name in Case.model_fields if name != "id"} | {"_id": 1}
TASK_FIELDS = {"_id": 1, "reason": 1, "allowed_actions": 1}


class CaseAlreadyExists(Exception):
    """The case ID (one per submission reference) is already stored."""


class HouseholdHasOpenCase(Exception):
    """The partial unique index on open cases refused the insert."""


CALENDAR_DATES = ("received_on", "decision_deadline", "decided_on")


def bson_date(day: date) -> datetime:
    """Calendar dates are stored as BSON dates at midnight UTC (migration 0002, database.md)."""
    return datetime.combine(day, time(0), tzinfo=UTC)


def to_bson(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: to_bson(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_bson(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, date) and not isinstance(value, datetime):
        return bson_date(value)
    return value


def _case_from_bson(doc: dict[str, Any]) -> Case:
    for name in CALENDAR_DATES:
        if isinstance(doc.get(name), datetime):
            doc[name] = doc[name].astimezone(UTC).date()
    return Case.model_validate(doc)


def _is_open_case_guard(error: DuplicateKeyError) -> bool:
    pattern = (error.details or {}).get("keyPattern") or {}
    return "household_id" in pattern


class MongoCaseTransaction:
    def __init__(self, client: Client, db_name: str, session: AsyncClientSession) -> None:
        self._db = client[db_name]
        self._session = session

    async def find_case(self, scope: Scope, case_id: UUID) -> Case | None:
        doc = await find_one(
            self._db, CASES, scope, {"_id": case_id}, projection=CASE_FIELDS, session=self._session
        )
        return _case_from_bson(doc) if doc else None

    async def coverage_ends_on_or_after(self, scope: Scope, household_id: UUID, day: date) -> bool:
        query = {"household_id": household_id, "end_date": {"$gte": bson_date(day)}}
        doc = await find_one(
            self._db, COVERAGE_PERIODS, scope, query, projection={"_id": 1}, session=self._session
        )
        return doc is not None

    async def insert_case(self, scope: Scope, case: Case) -> None:
        try:
            await insert_one(
                self._db,
                CASES,
                scope,
                to_bson(case.model_dump(by_alias=True)),
                session=self._session,
            )
        except DuplicateKeyError as error:
            if _is_open_case_guard(error):
                raise HouseholdHasOpenCase() from error
            raise CaseAlreadyExists() from error


class MongoCaseRepository:
    def __init__(self, client: Client, db_name: str) -> None:
        self._client = client
        self._db_name = db_name

    async def run_in_transaction(self, work: Callable[[MongoCaseTransaction], Awaitable[T]]) -> T:
        """Run `work` in one transaction; transient conflicts are retried by the driver."""
        async with self._client.start_session() as session:

            async def callback(s: AsyncClientSession) -> T:
                return await work(MongoCaseTransaction(self._client, self._db_name, s))

            return await session.with_transaction(callback)

    async def find_case(self, scope: Scope, case_id: UUID) -> Case | None:
        db = self._client[self._db_name]
        doc = await find_one(db, CASES, scope, {"_id": case_id}, projection=CASE_FIELDS)
        return _case_from_bson(doc) if doc else None

    async def open_review_task(self, scope: Scope, case_id: UUID) -> OpenReviewTask | None:
        query = {"case_id": case_id, "status": ReviewTaskStatus.open.value}
        db = self._client[self._db_name]
        doc = await find_one(db, REVIEW_TASKS, scope, query, projection=TASK_FIELDS)
        if doc is None:
            return None
        return OpenReviewTask(
            task_id=doc["_id"],
            reason=doc["reason"],
            allowed_actions=[CaseworkerActionType(a) for a in doc["allowed_actions"]],
        )

    async def record_rejected_action(
        self, scope: Scope, action: CaseworkerAction, entry: AuditLogEntry, audit: AuditWriter
    ) -> None:
        """A refusal the workflow couldn't record (ADR-001-04): the action and its audit entry in
        one transaction. Keyed by request_id, so a retried request writes neither again."""
        document = to_bson(action.model_dump(by_alias=True))
        limit = scoped_filter(scope, CASEWORKER_ACTIONS)
        if any(document.get(key) != expected for key, expected in limit.items()):
            raise Forbidden()
        document.pop("_id")  # set by the upsert from the filter
        db = self._client[self._db_name]

        async def write(session: AsyncClientSession) -> None:
            result = await db[CASEWORKER_ACTIONS].update_one(
                scoped_filter(scope, CASEWORKER_ACTIONS, {"_id": action.id}),
                {"$setOnInsert": document},
                upsert=True,
                session=session,
            )
            if result.upserted_id is not None:
                await audit.write(entry, session=session)

        async with self._client.start_session() as session:
            await session.with_transaction(write)
