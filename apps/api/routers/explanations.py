"""004a: decision explanations for caseworkers and applicants (FR-004-15, FR-004-16)."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from apps.api.dependencies.auth import request_id, require_roles
from apps.api.dependencies.cases import get_case_repository, get_explanation_repository
from apps.api.dependencies.db import get_audit_writer, get_clock
from apps.api.schemas.mvp import ApplicantHistory, ExplanationList
from apps.api.services import cases as cases_service
from apps.api.services import explanations as explanations_service
from packages.domain.auth import Principal, Role
from packages.domain.errors import ErrorEnvelope
from packages.platform.audit import AuditWriter
from packages.platform.clock import Clock

router = APIRouter(tags=["explanations"])

RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorEnvelope, "description": "Not signed in"},
    403: {"model": ErrorEnvelope, "description": "Role not allowed"},
    404: {"model": ErrorEnvelope, "description": "Not found or not visible to the caller"},
    422: {"model": ErrorEnvelope, "description": "validation_failed"},
}


@router.get(
    "/cases/{case_id}/explanations",
    operation_id="listCaseExplanations",
    responses=RESPONSES,
    summary="Caseworker explanations of a case's determinations, as stored (FR-004-15)",
)
async def list_case_explanations(
    case_id: UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require_roles(Role.caseworker))],
    cases: Annotated[cases_service.CaseRepository, Depends(get_case_repository)],
    explanations: Annotated[
        explanations_service.ExplanationReader, Depends(get_explanation_repository)
    ],
    audit: Annotated[AuditWriter, Depends(get_audit_writer)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> ExplanationList:
    return await explanations_service.list_case_explanations(
        case_id,
        principal,
        cases=cases,
        explanations=explanations,
        audit=audit,
        clock=clock,
        reference_id=request_id(request),
    )


@router.get(
    "/me/history",
    operation_id="getApplicantHistory",
    responses=RESPONSES,
    summary="The applicant's decisions per coverage period, with explanations (FR-004-16)",
)
async def get_applicant_history(
    principal: Annotated[Principal, Depends(require_roles(Role.applicant))],
    explanations: Annotated[
        explanations_service.ExplanationReader, Depends(get_explanation_repository)
    ],
) -> ApplicantHistory:
    return await explanations_service.applicant_history(principal, explanations=explanations)
