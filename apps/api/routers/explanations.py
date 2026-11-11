"""004a: decision explanations for caseworkers and applicants (FR-004-15, FR-004-16). Stubs."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from apps.api.dependencies.auth import require_roles
from apps.api.routers.contract_stubs import STUB_RESPONSES, not_built
from apps.api.schemas.mvp import ApplicantHistory, ExplanationList
from packages.domain.auth import Principal, Role

router = APIRouter(tags=["explanations"])


@router.get(
    "/cases/{case_id}/explanations",
    operation_id="listCaseExplanations",
    responses=STUB_RESPONSES,
    summary="Caseworker explanations of a case's determinations, as stored (FR-004-15)",
)
async def list_case_explanations(
    case_id: UUID,
    principal: Annotated[Principal, Depends(require_roles(Role.caseworker))],
) -> ExplanationList:
    not_built()


@router.get(
    "/me/history",
    operation_id="getApplicantHistory",
    responses=STUB_RESPONSES,
    summary="The applicant's decisions per coverage period, with explanations (FR-004-16)",
)
async def get_applicant_history(
    principal: Annotated[Principal, Depends(require_roles(Role.applicant))],
) -> ApplicantHistory:
    not_built()
