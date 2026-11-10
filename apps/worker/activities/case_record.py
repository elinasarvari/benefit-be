"""`record_case_change`: projects one workflow step into MongoDB (ADR-001-01, TW-9, TW-11).

Thin: builds the worker scope from the change's own case and household (ADR-001-06) and calls the
repository. The override reason is stored but never logged (PL-11): only IDs, `seq` and codes are.
"""

from __future__ import annotations

from typing import Protocol

import structlog
from temporalio import activity

from packages.domain.case import CaseChange
from packages.platform.scope import Scope

log = structlog.get_logger()


class CaseChangeWriter(Protocol):
    async def apply(self, change: CaseChange, scope: Scope) -> bool: ...


class CaseRecordActivities:
    """Built once per worker process (TW-12) with the repository injected."""

    def __init__(self, repository: CaseChangeWriter) -> None:
        self._repository = repository

    @activity.defn(name="record_case_change")
    async def record_case_change(self, change: CaseChange) -> None:
        scope = Scope.for_worker(change.case_id, change.household_id)
        applied = await self._repository.apply(change, scope)
        log.info(
            "case_change.recorded",
            case_id=str(change.case_id),
            workflow_id=activity.info().workflow_id,
            seq=change.seq,
            kind=change.event.kind.value,
            reason_code=change.event.reason_code,
            already_applied=not applied,
        )
