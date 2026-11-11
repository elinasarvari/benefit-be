from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response

from apps.api.dependencies.auth import require_roles
from apps.api.dependencies.cases import get_case_repository, get_case_workflows
from apps.api.dependencies.db import get_clock, get_settings
from apps.api.schemas.cases import ApplicationSubmission, CaseStatus
from apps.api.services import cases as cases_service
from packages.domain.auth import Principal, Role
from packages.domain.errors import ErrorEnvelope
from packages.platform.clock import Clock
from packages.platform.settings import Settings

OPENAPI_TAG = "cases"
router = APIRouter(tags=[OPENAPI_TAG])

SUBMIT_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {"model": CaseStatus, "description": "Same submission reference already opened this case"},
    401: {"model": ErrorEnvelope, "description": "Not signed in"},
    403: {"model": ErrorEnvelope, "description": "Role not allowed"},
    409: {
        "model": ErrorEnvelope,
        "description": "Refused: error.code=conflict, reason in error.fields[0].type "
        "(household_has_open_case | household_has_coverage | submission_reference_in_use); "
        "error.related_case_id set for the first two",
    },
    422: {
        "model": ErrorEnvelope,
        "description": "validation_failed; error.fields[].field is the dotted form path",
    },
    503: {"model": ErrorEnvelope, "description": "Temporal unavailable; retry the same submission"},
}


@router.post(
    "/applications",
    operation_id="submitApplication",
    status_code=201,
    responses=SUBMIT_RESPONSES,
    summary="Submit an application; opens one case per submission reference",
)
async def submit_application(
    body: ApplicationSubmission,
    response: Response,
    principal: Annotated[Principal, Depends(require_roles(Role.applicant))],
    repo: Annotated[cases_service.CaseRepository, Depends(get_case_repository)],
    workflows: Annotated[cases_service.CaseWorkflows, Depends(get_case_workflows)],
    clock: Annotated[Clock, Depends(get_clock)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CaseStatus:
    result = await cases_service.submit_application(
        body,
        principal,
        repo=repo,
        workflows=workflows,
        clock=clock,
        demo_profile=settings.DEMO_PROFILE,
    )
    if not result.created:
        response.status_code = 200
    return result.status
