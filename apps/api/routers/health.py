from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from apps.api.services.readiness import build_checks
from packages.domain.health import HealthReport, ReadinessReport
from packages.platform.health import Check, build_report, health_report, readiness_response
from packages.platform.settings import Settings

router = APIRouter(tags=["health"])


def get_checks(request: Request) -> list[Check]:
    settings: Settings = request.app.state.settings
    return build_checks(settings)


@router.get("/health", operation_id="getHealth", response_model=HealthReport)
async def health() -> HealthReport:
    return health_report("api")


@router.get(
    "/ready",
    operation_id="getReadiness",
    responses={
        200: {"model": ReadinessReport, "description": "ready or degraded"},
        503: {"model": ReadinessReport, "description": "not_ready"},
    },
)
async def ready(checks: Annotated[list[Check], Depends(get_checks)]) -> JSONResponse:
    return readiness_response(await build_report("api", checks))
