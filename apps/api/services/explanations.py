"""Read stored explanations for caseworkers and the applicant's history (FR-004-15, FR-004-16).

Nothing is built here: the text is returned as it was stored (FR-004-13), including for a case a
caseworker overrode (ADR-004-04). The caseworker read is audited like 003's determination read
(ADR-004-03); the entry holds IDs and codes only (PL-11). An applicant reading their own
history is not audited.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

import structlog

from apps.api.repositories.explanations import HistoryRow
from apps.api.schemas.mvp import ApplicantHistory, ExplanationList, HistoryEntry
from packages.domain.audit import ActorType, AuditAction, AuditLogEntry, AuditOutcome
from packages.domain.auth import Principal
from packages.domain.case import Case
from packages.domain.explanation import DecisionExplanation
from packages.platform.audit import AuditWriter
from packages.platform.clock import Clock
from packages.platform.errors import NotFound
from packages.platform.logging import bind_context
from packages.platform.scope import Scope

log = structlog.get_logger()


class ExplanationReader(Protocol):
    async def list_for_case(self, scope: Scope, case_id: UUID) -> list[DecisionExplanation]: ...

    async def history(self, scope: Scope, household_id: UUID) -> list[HistoryRow]: ...


class CaseFinder(Protocol):
    async def find_case(self, scope: Scope, case_id: UUID) -> Case | None: ...


async def list_case_explanations(
    case_id: UUID,
    principal: Principal,
    *,
    cases: CaseFinder,
    explanations: ExplanationReader,
    audit: AuditWriter,
    clock: Clock,
    reference_id: str,
) -> ExplanationList:
    bind_context(case_id=str(case_id))
    scope = Scope.for_principal(principal)
    if await cases.find_case(scope, case_id) is None:
        raise NotFound()
    items = await explanations.list_for_case(scope, case_id)
    await audit.write(
        AuditLogEntry(
            occurred_at=clock.now(),
            actor_type=ActorType.user,
            actor_id=str(principal.user_id),
            actor_role=principal.role,
            action=AuditAction.determinations_read,
            target_type="case_explanations",
            target_id=str(case_id),
            outcome=AuditOutcome.success,
            reference_id=reference_id,
        )
    )
    log.info("explanations.read", count=len(items))
    return ExplanationList(items=items)


async def applicant_history(
    principal: Principal, *, explanations: ExplanationReader
) -> ApplicantHistory:
    if principal.household_id is None:
        raise NotFound()
    scope = Scope.for_principal(principal)
    rows = await explanations.history(scope, principal.household_id)
    log.info("history.read", count=len(rows))
    return ApplicantHistory(
        items=[
            HistoryEntry(
                case_id=row.case.case_id,
                case_type=row.case.case_type,
                outcome=row.case.outcome,
                decided_on=row.case.decided_on,
                coverage_start=row.coverage.start_date if row.coverage else None,
                coverage_end=row.coverage.end_date if row.coverage else None,
                explanation=row.explanation,
            )
            for row in rows
        ]
    )
