"""002: pay stub upload and the check result (FR-002-17, FR-002-18, FR-002-21). Contract stubs."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from apps.api.dependencies.auth import require_roles
from apps.api.routers.contract_stubs import STUB_RESPONSES, not_built
from apps.api.schemas.mvp import (
    MAX_UPLOAD_BYTES,
    CaseVerification,
    DocumentCreated,
    PayStubUpload,
)
from packages.domain.auth import Principal, Role
from packages.domain.errors import ErrorEnvelope

router = APIRouter(prefix="/cases", tags=["documents"])

CONTENT_RESPONSES: dict[int | str, dict[str, Any]] = {
    **STUB_RESPONSES,
    204: {"description": "File stored"},
    413: {"model": ErrorEnvelope, "description": f"Larger than {MAX_UPLOAD_BYTES} bytes"},
    415: {"model": ErrorEnvelope, "description": "Not PDF, PNG or JPEG"},
}
BINARY_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            media: {"schema": {"type": "string", "format": "binary"}}
            for media in ("application/pdf", "image/png", "image/jpeg")
        },
    }
}


@router.post(
    "/{case_id}/documents",
    operation_id="createPayStubDocument",
    status_code=201,
    responses=STUB_RESPONSES,
    summary="Record a pay stub's typed details; the file follows to .../content (FR-002-21)",
)
async def create_pay_stub_document(
    case_id: UUID,
    body: PayStubUpload,
    principal: Annotated[Principal, Depends(require_roles(Role.applicant))],
) -> DocumentCreated:
    not_built()


@router.put(
    "/{case_id}/documents/{document_id}/content",
    operation_id="uploadDocumentContent",
    status_code=204,
    response_class=Response,
    responses=CONTENT_RESPONSES,
    openapi_extra=BINARY_BODY,
    summary="Upload the file of a recorded pay stub: PDF, PNG or JPEG, up to 10 MB (FR-002-17)",
)
async def upload_document_content(
    case_id: UUID,
    document_id: UUID,
    principal: Annotated[Principal, Depends(require_roles(Role.applicant))],
) -> Response:
    not_built()


@router.get(
    "/{case_id}/verification",
    operation_id="getCaseVerification",
    responses=STUB_RESPONSES,
    summary="Documents, entered details and findings of a case (FR-002-18)",
)
async def get_case_verification(
    case_id: UUID,
    principal: Annotated[Principal, Depends(require_roles(Role.caseworker))],
) -> CaseVerification:
    not_built()
