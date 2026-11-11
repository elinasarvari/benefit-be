from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from apps.api.dependencies.auth import request_id, require_roles
from apps.api.dependencies.cases import (
    get_case_repository,
    get_case_workflows,
    get_determination_repository,
    get_user_names,
)
from apps.api.dependencies.db import get_audit_writer, get_clock
from apps.api.schemas.cases import (
    CaseDetail,
    CaseHistoryPage,
    CasePage,
    CaseState,
    ReviewActionBody,
)
from apps.api.schemas.determinations import DeterminationList
from apps.api.services import cases as cases_service
from apps.api.services import determinations as determinations_service
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
    "",
    operation_id="listCases",
    responses=COMMON,
    summary="Cases with state, days remaining and at-risk flag, soonest deadline first",
)
async def list_cases(
    principal: Annotated[
        Principal, Depends(require_roles(Role.applicant, Role.caseworker, Role.supervisor))
    ],
    repo: Annotated[cases_service.CaseRepository, Depends(get_case_repository)],
    clock: Annotated[Clock, Depends(get_clock)],
    state: CaseState | None = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = cases_service.DEFAULT_LIST_LIMIT,
) -> CasePage:
    return await cases_service.list_cases(
        principal, repo=repo, clock=clock, state=state, cursor=cursor, limit=limit
    )


@router.get(
    "/{case_id}",
    operation_id="getCase",
    responses=COMMON,
    summary="Case state, dates, outcome, coverage period and override",
)
async def get_case(
    case_id: UUID,
    request: Request,
    principal: Annotated[
        Principal, Depends(require_roles(Role.applicant, Role.caseworker, Role.supervisor))
    ],
    repo: Annotated[cases_service.CaseRepository, Depends(get_case_repository)],
    users: Annotated[cases_service.UserNames, Depends(get_user_names)],
    audit: Annotated[AuditWriter, Depends(get_audit_writer)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> CaseDetail:
    return await cases_service.get_case_detail(
        case_id,
        principal,
        repo=repo,
        users=users,
        audit=audit,
        clock=clock,
        reference_id=request_id(request),
    )


@router.get(
    "/{case_id}/history",
    operation_id="getCaseHistory",
    responses=COMMON,
    summary="Case history with case-clock and real time, actor and reason",
)
async def get_case_history(
    case_id: UUID,
    principal: Annotated[Principal, Depends(require_roles(Role.caseworker))],
    repo: Annotated[cases_service.CaseRepository, Depends(get_case_repository)],
    users: Annotated[cases_service.UserNames, Depends(get_user_names)],
    cursor: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = cases_service.DEFAULT_HISTORY_LIMIT,
) -> CaseHistoryPage:
    return await cases_service.get_case_history(
        case_id, principal, repo=repo, users=users, cursor=cursor, limit=limit
    )


@router.get(
    "/{case_id}/determinations",
    operation_id="listCaseDeterminations",
    responses=COMMON,
    summary="Determinations of a case, newest first, with the per-rule trace (FR-003-23)",
)
async def list_case_determinations(
    case_id: UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require_roles(Role.caseworker))],
    cases: Annotated[cases_service.CaseRepository, Depends(get_case_repository)],
    determinations: Annotated[
        determinations_service.DeterminationReader, Depends(get_determination_repository)
    ],
    audit: Annotated[AuditWriter, Depends(get_audit_writer)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> DeterminationList:
    return await determinations_service.list_case_determinations(
        case_id,
        principal,
        cases=cases,
        determinations=determinations,
        audit=audit,
        clock=clock,
        reference_id=request_id(request),
    )


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
