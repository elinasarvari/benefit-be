"""007a: the household page and on-demand redeterminations (FR-007-10, FR-007-18)."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from apps.api.dependencies.auth import request_id, require_roles
from apps.api.dependencies.cases import get_case_repository, get_case_workflows
from apps.api.dependencies.db import get_audit_writer, get_clock, get_settings
from apps.api.dependencies.households import get_household_repository
from apps.api.schemas.mvp import HouseholdView, RedeterminationStart, RedeterminationStarted
from apps.api.services import households as households_service
from apps.api.services import redeterminations as redeterminations_service
from apps.api.services.cases import CaseRepository, CaseWorkflows
from packages.domain.auth import Principal, Role
from packages.domain.errors import ErrorEnvelope
from packages.platform.audit import AuditWriter
from packages.platform.clock import Clock
from packages.platform.settings import Settings

router = APIRouter(prefix="/households", tags=["households"])

COMMON: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorEnvelope, "description": "Not signed in"},
    403: {"model": ErrorEnvelope, "description": "Role not allowed"},
    422: {"model": ErrorEnvelope, "description": "validation_failed"},
}
START_RESPONSES: dict[int | str, dict[str, Any]] = {
    **COMMON,
    200: {
        "model": RedeterminationStarted,
        "description": "Already running for this coverage period (FR-007-07)",
    },
    409: {
        "model": ErrorEnvelope,
        "description": (
            "no_active_coverage_period | household_has_open_case "
            "(the open case_id is in fields; ADR-007-06)"
        ),
    },
    503: {"model": ErrorEnvelope, "description": "Temporal unavailable"},
}


@router.get(
    "/{household_id}",
    operation_id="getHousehold",
    responses={
        **COMMON,
        404: {"model": ErrorEnvelope, "description": "not_found: no members"},
    },
    summary="Members, coverage periods and cases of a household (FR-007-18)",
)
async def get_household(
    household_id: UUID,
    principal: Annotated[Principal, Depends(require_roles(Role.caseworker))],
    repo: Annotated[households_service.HouseholdRepository, Depends(get_household_repository)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> HouseholdView:
    return await households_service.get_household(household_id, principal, repo=repo, clock=clock)


@router.post(
    "/{household_id}/redeterminations",
    operation_id="startRedetermination",
    status_code=201,
    responses=START_RESPONSES,
    summary="Start a coverage period's redetermination, or link to the running one (FR-007-10)",
)
async def start_redetermination(
    household_id: UUID,
    body: RedeterminationStart,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(require_roles(Role.caseworker))],
    cases: Annotated[CaseRepository, Depends(get_case_repository)],
    households: Annotated[
        households_service.HouseholdRepository, Depends(get_household_repository)
    ],
    workflows: Annotated[CaseWorkflows, Depends(get_case_workflows)],
    audit: Annotated[AuditWriter, Depends(get_audit_writer)],
    clock: Annotated[Clock, Depends(get_clock)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RedeterminationStarted:
    result = await redeterminations_service.start_redetermination(
        household_id,
        body,
        principal,
        cases=cases,
        households=households,
        workflows=workflows,
        audit=audit,
        clock=clock,
        demo_profile=settings.DEMO_PROFILE,
        reference_id=request_id(request),
    )
    if not result.created:
        response.status_code = 200
    return RedeterminationStarted(case_id=result.case_id, created=result.created)
