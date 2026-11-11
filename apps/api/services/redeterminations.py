"""On-demand start of a coverage period's redetermination (007a, FR-007-07, FR-007-10).

One case per household and period (`redetermination_case_id`), so a second press, the daily
schedule and a retry after Temporal was down all meet the same case and workflow (TW-6).
No log line carries household data (PL-11).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import structlog

from apps.api.repositories.cases import CaseAlreadyExists, HouseholdHasOpenCase
from apps.api.schemas.mvp import RedeterminationStart
from apps.api.services.cases import DEMO_SCALE, CaseRepository, CaseTransaction, CaseWorkflows
from apps.api.services.cases import start_of as case_start_of
from apps.api.services.households import HouseholdRepository
from packages.domain.audit import ActorType, AuditAction, AuditLogEntry, AuditOutcome
from packages.domain.auth import Principal
from packages.domain.case import (
    Case,
    CoverageStatus,
    new_redetermination_case,
    redetermination_case_id,
    workflow_id_for,
)
from packages.domain.errors import FieldError
from packages.platform.audit import AuditWriter
from packages.platform.clock import Clock
from packages.platform.errors import Conflict
from packages.platform.logging import bind_context
from packages.platform.scope import Scope

STARTABLE = (CoverageStatus.active, CoverageStatus.continued_pending_review)
NO_ACTIVE_PERIOD = "no_active_coverage_period"
OPEN_CASE = "household_has_open_case"

log = structlog.get_logger()


@dataclass(frozen=True)
class StartResult:
    case_id: UUID
    created: bool


def _refusal(code: str, message: str, related_case_id: UUID | None = None) -> Conflict:
    return Conflict(
        message,
        related_case_id=related_case_id,
        fields=[FieldError(field="coverage_period_id", message=message, type=code)],
    )


async def start_redetermination(
    household_id: UUID,
    body: RedeterminationStart,
    principal: Principal,
    *,
    cases: CaseRepository,
    households: HouseholdRepository,
    workflows: CaseWorkflows,
    audit: AuditWriter,
    clock: Clock,
    demo_profile: bool,
    reference_id: str,
) -> StartResult:
    """Store the case and its audit entry in one transaction, then start the workflow on every
    call: a retry after Temporal was down starts it, and an already started workflow is success."""
    scope = Scope.for_principal(principal)
    case_id = redetermination_case_id(household_id, body.coverage_period_id)
    bind_context(case_id=str(case_id), workflow_id=workflow_id_for(case_id))
    case = await cases.find_case(scope, case_id)
    created = False
    if case is None:
        case, created = await _open_case(
            household_id,
            body,
            principal,
            scope,
            cases,
            households,
            audit,
            clock,
            demo_profile,
            reference_id,
        )
    await workflows.start(case_start_of(case))
    log.info("redetermination.started", created=created)
    return StartResult(case_id, created)


async def _open_case(
    household_id: UUID,
    body: RedeterminationStart,
    principal: Principal,
    scope: Scope,
    cases: CaseRepository,
    households: HouseholdRepository,
    audit: AuditWriter,
    clock: Clock,
    demo_profile: bool,
    reference_id: str,
) -> tuple[Case, bool]:
    period = await households.find_period(scope, household_id, body.coverage_period_id)
    if period is None or period.status not in STARTABLE:
        log.info("redetermination.refused", reason_code=NO_ACTIVE_PERIOD)
        raise _refusal(NO_ACTIVE_PERIOD, "This coverage period cannot be redetermined.")
    candidate = new_redetermination_case(
        household_id, period, clock.now(), DEMO_SCALE if demo_profile else 1
    )

    async def insert(tx: CaseTransaction) -> None:
        await tx.insert_case(scope, candidate)
        await tx.write_audit(
            AuditLogEntry(
                occurred_at=clock.now(),
                actor_type=ActorType.user,
                actor_id=str(principal.user_id),
                actor_role=principal.role,
                action=AuditAction.redetermination_started_on_demand,
                target_type="case",
                target_id=str(candidate.case_id),
                outcome=AuditOutcome.success,
                reference_id=reference_id,
            ),
            audit,
        )

    try:
        await cases.run_in_transaction(insert)
    except HouseholdHasOpenCase:
        # The aborted transaction can't read; the open case is looked up afterwards.
        open_id = await cases.find_open_case_id(scope, household_id)
        log.info("redetermination.refused", reason_code=OPEN_CASE)
        raise _refusal(OPEN_CASE, "This household already has an open case.", open_id) from None
    except CaseAlreadyExists:
        found = await cases.find_case(scope, candidate.case_id)
        if found is None:
            raise
        return found, False  # a concurrent request stored the case first
    return candidate, True
