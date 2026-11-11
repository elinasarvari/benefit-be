"""007a: the household page and on-demand redeterminations (FR-007-10, FR-007-18). Stubs."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends

from apps.api.dependencies.auth import require_roles
from apps.api.routers.contract_stubs import STUB_RESPONSES, not_built
from apps.api.schemas.mvp import HouseholdView, RedeterminationStart, RedeterminationStarted
from packages.domain.auth import Principal, Role
from packages.domain.errors import ErrorEnvelope

router = APIRouter(prefix="/households", tags=["households"])

START_RESPONSES: dict[int | str, dict[str, Any]] = {
    **STUB_RESPONSES,
    200: {
        "model": RedeterminationStarted,
        "description": "Already running for this coverage period (FR-007-07)",
    },
    409: {"model": ErrorEnvelope, "description": "No active coverage period with that ID"},
    503: {"model": ErrorEnvelope, "description": "Temporal unavailable"},
}


@router.get(
    "/{household_id}",
    operation_id="getHousehold",
    responses=STUB_RESPONSES,
    summary="Members, coverage periods and cases of a household (FR-007-18)",
)
async def get_household(
    household_id: UUID,
    principal: Annotated[Principal, Depends(require_roles(Role.caseworker))],
) -> HouseholdView:
    not_built()


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
    principal: Annotated[Principal, Depends(require_roles(Role.caseworker))],
) -> RedeterminationStarted:
    not_built()
