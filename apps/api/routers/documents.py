"""002: pay stub upload and the check result (FR-002-17, FR-002-18, FR-002-21)."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from apps.api.dependencies.auth import require_roles
from apps.api.dependencies.cases import get_case_repository
from apps.api.dependencies.db import get_clock
from apps.api.dependencies.documents import get_blob_store, get_document_repository
from apps.api.schemas.mvp import (
    MAX_UPLOAD_BYTES,
    CaseVerification,
    DocumentCreated,
    PayStubUpload,
)
from apps.api.services import cases as cases_service
from apps.api.services import documents as documents_service
from packages.domain.auth import Principal, Role
from packages.domain.errors import ErrorEnvelope
from packages.platform.blobs import BlobStore
from packages.platform.clock import Clock

# The tag is not a collection name, so the scope check (FR-000-19) does not mistake it for one.
router = APIRouter(prefix="/cases", tags=["pay-stubs"])

COMMON: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorEnvelope, "description": "Not signed in"},
    403: {"model": ErrorEnvelope, "description": "Role not allowed, or another household's case"},
    404: {"model": ErrorEnvelope, "description": "Not found or not visible to the caller"},
    422: {"model": ErrorEnvelope, "description": "validation_failed"},
}
CONTENT_RESPONSES: dict[int | str, dict[str, Any]] = {
    **COMMON,
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
    responses=COMMON,
    summary="Record a pay stub's typed details; the file follows to .../content (FR-002-21)",
)
async def create_pay_stub_document(
    case_id: UUID,
    body: PayStubUpload,
    principal: Annotated[Principal, Depends(require_roles(Role.applicant))],
    documents: Annotated[documents_service.DocumentRepository, Depends(get_document_repository)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> DocumentCreated:
    return await documents_service.create_pay_stub(
        case_id, body, principal, documents=documents, clock=clock
    )


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
    request: Request,
    principal: Annotated[Principal, Depends(require_roles(Role.applicant))],
    documents: Annotated[documents_service.DocumentRepository, Depends(get_document_repository)],
    blobs: Annotated[BlobStore, Depends(get_blob_store)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> Response:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise documents_service.PayloadTooLarge(
            f"The file is too large. {documents_service.LIMITS_TEXT}"
        )
    data = await documents_service.read_capped(request.stream())
    await documents_service.upload_content(
        case_id,
        document_id,
        request.headers.get("content-type"),
        data,
        principal,
        documents=documents,
        blobs=blobs,
        clock=clock,
    )
    return Response(status_code=204)


@router.get(
    "/{case_id}/verification",
    operation_id="getCaseVerification",
    responses=COMMON,
    summary="Documents, entered details and findings of a case (FR-002-18)",
)
async def get_case_verification(
    case_id: UUID,
    principal: Annotated[Principal, Depends(require_roles(Role.caseworker))],
    cases: Annotated[cases_service.CaseRepository, Depends(get_case_repository)],
    documents: Annotated[documents_service.DocumentRepository, Depends(get_document_repository)],
) -> CaseVerification:
    return await documents_service.get_case_verification(
        case_id, principal, cases=cases, documents=documents
    )
