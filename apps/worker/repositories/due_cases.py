"""Open the due redeterminations: a case and its audit entry in one transaction per period
(007, FR-007-01, FR-007-07, ADR-007-06).

The case ID is derived from the household and period, so the API's on-demand start and this
scan meet the same case. A household with another open case is skipped and picked up the next day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.errors import DuplicateKeyError

from apps.worker.repositories.cases import CASES, to_bson
from packages.domain.audit import ActorType, AuditAction, AuditLogEntry, AuditOutcome
from packages.domain.case import (
    CaseClock,
    CaseStart,
    CaseType,
    CoveragePeriod,
    new_redetermination_case,
)
from packages.platform.audit import AuditWriter
from packages.platform.mongo import Client
from packages.platform.scope import Scope, scoped_filter

SYSTEM_ACTOR = "redeterminations-due"


class Opened(StrEnum):
    created = "created"
    existing = "existing"  # this period's case is already open
    skipped = "skipped"  # another case of the household is open


@dataclass(frozen=True)
class OpenResult:
    opened: Opened
    start: CaseStart | None  # what to start a workflow from; None when skipped


class DueCases:
    def __init__(self, client: Client, db_name: str, audit: AuditWriter) -> None:
        self._client = client
        self._db = client[db_name]
        self._audit = audit

    async def open_case(self, period: CoveragePeriod, now: datetime, scale: int) -> OpenResult:
        candidate = new_redetermination_case(period.household_id, period, now, scale)
        scope = Scope.for_worker(candidate.case_id, candidate.household_id)

        async def write(session: AsyncClientSession) -> None:
            await self._db[CASES].insert_one(
                to_bson(candidate.model_dump(by_alias=True)), session=session
            )
            await self._audit.write(
                AuditLogEntry(
                    occurred_at=now,
                    actor_type=ActorType.system,
                    actor_id=SYSTEM_ACTOR,
                    action=AuditAction.redetermination_started_by_schedule,
                    target_type="case",
                    target_id=str(candidate.case_id),
                    outcome=AuditOutcome.success,
                    reference_id=str(candidate.case_id),
                ),
                session=session,
            )

        async with self._client.start_session() as session:
            try:
                await session.with_transaction(write)
            except DuplicateKeyError as error:
                pattern = (error.details or {}).get("keyPattern") or {}
                if "household_id" in pattern:
                    return OpenResult(Opened.skipped, None)
                return await self._existing(scope, candidate.case_id, period)
        return OpenResult(
            Opened.created,
            CaseStart(
                case_id=candidate.case_id,
                household_id=candidate.household_id,
                case_type=CaseType.redetermination,
                submission_reference=candidate.submission_reference,
                received_at=now,
                clock=candidate.clock,
                coverage_period_id=period.id,
            ),
        )

    async def _existing(self, scope: Scope, case_id: UUID, period: CoveragePeriod) -> OpenResult:
        """The case stored earlier: start its workflow again only while it is open."""
        doc = await self._db[CASES].find_one(
            scoped_filter(scope, CASES, {"_id": case_id}),
            {"open": 1, "clock": 1, "created_at": 1, "submission_reference": 1},
        )
        if doc is None or not doc.get("open", False):
            return OpenResult(Opened.existing, None)
        return OpenResult(
            Opened.existing,
            CaseStart(
                case_id=case_id,
                household_id=period.household_id,
                case_type=CaseType.redetermination,
                submission_reference=doc["submission_reference"],
                received_at=doc["created_at"],
                clock=CaseClock.model_validate(doc["clock"]),
                coverage_period_id=period.id,
            ),
        )
