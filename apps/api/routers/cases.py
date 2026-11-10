from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from apps.api.dependencies.auth import require_roles
from apps.api.dependencies.cases import get_case_repository, get_case_workflows
from apps.api.dependencies.db import get_audit_writer, get_clock
from apps.api.schemas.cases import CaseStatus, ReviewActionBody
from apps.api.services import cases as cases_service
from packages.domain.auth import Principal, Role
from packages.domain.errors import ErrorEnvelope
from packages.platform.audit import AuditWriter
from packages.platform.clock import Clock

OPENAPI_TAG = "cases"
router = APIRouter(prefix="/cases", tags=[OPENAPI_TAG])

COMMON: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorEnvelope, "description": "Not signed in"},
    403: {"model": ErrorEnvelope, "description": "Role not allowed"},
    404: {"model": ErrorEnvelope, "description": "Not found or not visible to the caller"},
    422: {"model": ErrorEnvelope, "description": "validation_failed"},
}
ACTION_RESPONSES: dict[int | str, dict[str, Any]] = {
    **COMMON,
    202: {"description": "Action accepted and signalled"},
    409: {
        "model": ErrorEnvelope,
        "description": "Action not allowed for this task or state; recorded as rejected",
    },
    503: {"model": ErrorEnvelope, "description": "Temporal unavailable"},
}


@router.get(
    "/{case_id}",
    operation_id="getCase",
    responses=COMMON,
    summary="Case state, deadline, days remaining and at-risk flag",
)
async def get_case(
    case_id: UUID,
    principal: Annotated[Principal, Depends(require_roles(Role.applicant, Role.caseworker))],
    repo: Annotated[cases_service.CaseRepository, Depends(get_case_repository)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> CaseStatus:
    return await cases_service.get_case_status(case_id, principal, repo=repo, clock=clock)


@router.post(
    "/{case_id}/review-tasks/{task_id}/actions",
    operation_id="submitReviewAction",
    status_code=202,
    response_class=Response,
    responses=ACTION_RESPONSES,
    summary="Caseworker action on an open review task",
)
async def submit_review_action(
    case_id: UUID,
    task_id: UUID,
    body: ReviewActionBody,
    principal: Annotated[Principal, Depends(require_roles(Role.caseworker))],
    workflows: Annotated[cases_service.CaseWorkflows, Depends(get_case_workflows)],
    repo: Annotated[cases_service.CaseRepository, Depends(get_case_repository)],
    audit: Annotated[AuditWriter, Depends(get_audit_writer)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> Response:
    await cases_service.submit_review_action(
        case_id, task_id, body, principal, workflows=workflows, repo=repo, audit=audit, clock=clock
    )
    return Response(status_code=202)
