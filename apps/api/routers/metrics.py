"""008: program metrics, computed on request (FR-008-03, FR-008-09). Contract stub."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from apps.api.dependencies.auth import require_roles
from apps.api.routers.contract_stubs import STUB_RESPONSES, not_built
from apps.api.schemas.mvp import MetricsReport
from packages.domain.auth import Principal, Role

router = APIRouter(tags=["metrics"])


@router.get(
    "/metrics",
    operation_id="getProgramMetrics",
    responses=STUB_RESPONSES,
    summary="The four program metrics for one month, by case-clock dates (FR-008-08)",
)
async def get_program_metrics(
    principal: Annotated[Principal, Depends(require_roles(Role.caseworker))],
    month: Annotated[str, Query(pattern=r"^\d{4}-\d{2}$")],
) -> MetricsReport:
    not_built()
