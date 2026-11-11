"""Read a case's determinations with the per-rule trace (FR-003-23, ADR-003-05).

Caseworkers and supervisors only (PL-7); the read is audited (research R11). Rule inputs hold
member and job IDs, never names (FR-003-05, constitution VIII), so the stored trace is returned as
it is. No log line carries household data.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

import structlog

from apps.api.schemas.determinations import DeterminationList
from packages.domain.audit import ActorType, AuditAction, AuditLogEntry, AuditOutcome
from packages.domain.auth import Principal
from packages.domain.case import Case, DenialReason
from packages.domain.eligibility import (
    DeterminationRecord,
    DeterminationView,
    RuleId,
    RuleOutcome,
)
from packages.platform.audit import AuditWriter
from packages.platform.clock import Clock
from packages.platform.errors import NotFound
from packages.platform.logging import bind_context
from packages.platform.scope import Scope

log = structlog.get_logger()


class DeterminationReader(Protocol):
    async def list_for_case(self, scope: Scope, case_id: UUID) -> list[DeterminationRecord]: ...


class CaseFinder(Protocol):
    async def find_case(self, scope: Scope, case_id: UUID) -> Case | None: ...


def reason_code_of(record: DeterminationRecord) -> DenialReason | None:
    """Income if ELIG-06 failed, else another rule if any failed, else none (research R5)."""
    failed = {r.rule_id for r in record.determination.rule_results if r.outcome is RuleOutcome.fail}
    if RuleId.ELIG_06 in failed:
        return DenialReason.income_over_limit
    return DenialReason.other_rule_not_met if failed else None


def view_of(record: DeterminationRecord) -> DeterminationView:
    determination = record.determination
    return DeterminationView(
        determination_id=record.determination_id,
        result=determination.result,
        reason_code=reason_code_of(record),
        as_of_date=determination.as_of_date,
        rule_set_id=determination.rule_set_id,
        rule_set_version=determination.rule_set_version,
        household_size=determination.household_size,
        household_monthly_income=determination.household_monthly_income,
        monthly_income_limit=determination.monthly_income_limit,
        rules=determination.rule_results,
        missing_items=determination.missing_items,
        recorded_at=record.recorded_at,
    )


async def list_case_determinations(
    case_id: UUID,
    principal: Principal,
    *,
    cases: CaseFinder,
    determinations: DeterminationReader,
    audit: AuditWriter,
    clock: Clock,
    reference_id: str,
) -> DeterminationList:
    bind_context(case_id=str(case_id))
    scope = Scope.for_principal(principal)
    if await cases.find_case(scope, case_id) is None:
        raise NotFound()
    records = await determinations.list_for_case(scope, case_id)
    await audit.write(
        AuditLogEntry(
            occurred_at=clock.now(),
            actor_type=ActorType.user,
            actor_id=str(principal.user_id),
            actor_role=principal.role,
            action=AuditAction.determinations_read,
            target_type="case",
            target_id=str(case_id),
            outcome=AuditOutcome.success,
            reference_id=reference_id,
        )
    )
    log.info("determinations.read", count=len(records))
    return DeterminationList(items=[view_of(r) for r in records])
