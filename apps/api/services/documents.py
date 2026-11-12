"""Pay stub upload and the case page's documents section (002: FR-002-17, FR-002-18, FR-002-21).

An applicant uploads against `case_id_for(reference)` before submitting (ADR-002-01): the case may
not exist yet, but if it, or an earlier upload, belongs to another household the answer is 403. The
household always comes from the session (PL-6). Files go through `BlobStore` (KD-14). No log line
carries an employee name, an employer, a reason or file bytes (PL-11).
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

import anyio.to_thread
import structlog

from apps.api.repositories.documents import StoredDocument
from apps.api.schemas.mvp import (
    MAX_UPLOAD_BYTES,
    MAX_UPLOADS,
    CaseVerification,
    DocumentCreated,
    DocumentView,
    PayStubUpload,
)
from packages.domain.auth import Principal, Role
from packages.domain.case import Case
from packages.domain.errors import FieldError
from packages.domain.verification import PayStubDetails, VerificationResult
from packages.platform.blobs import BlobStore
from packages.platform.clock import Clock
from packages.platform.errors import AppError, Forbidden, InvalidInput, NotFound
from packages.platform.logging import bind_context
from packages.platform.scope import Scope

log = structlog.get_logger()

ALLOWED_TYPES = ("application/pdf", "image/png", "image/jpeg")
MAGIC = {
    "application/pdf": (b"%PDF",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
}
LIMITS_TEXT = "Files must be PDF, PNG or JPEG, up to 10 MB each, at most 10 per case."


class PayloadTooLarge(AppError):
    code = InvalidInput.code
    http_status = 413


class UnsupportedMediaType(AppError):
    code = InvalidInput.code
    http_status = 415


class DocumentRepository(Protocol):
    async def owning_household(self, case_id: UUID) -> UUID | None: ...

    async def count_documents(self, scope: Scope, case_id: UUID) -> int: ...

    async def insert_stub(
        self, scope: Scope, document: StoredDocument, details: PayStubDetails
    ) -> None: ...

    async def find_document(
        self, scope: Scope, case_id: UUID, document_id: UUID
    ) -> StoredDocument | None: ...

    async def mark_content(
        self,
        scope: Scope,
        document_id: UUID,
        *,
        size_bytes: int,
        sha256: str,
        blob_key: str,
        at: datetime,
    ) -> None: ...

    async def list_documents(
        self, scope: Scope, case_id: UUID
    ) -> list[tuple[StoredDocument, PayStubDetails]]: ...

    async def find_result(self, scope: Scope, case_id: UUID) -> VerificationResult | None: ...


class CaseFinder(Protocol):
    async def find_case(self, scope: Scope, case_id: UUID) -> Case | None: ...


def blob_key(household_id: UUID, document_id: UUID) -> str:
    return f"{household_id}/{document_id}"


async def read_capped(chunks: AsyncIterator[bytes]) -> bytes:
    """The request body, refused as soon as it passes the limit (FR-002-17)."""
    data = bytearray()
    async for chunk in chunks:
        data += chunk
        if len(data) > MAX_UPLOAD_BYTES:
            raise PayloadTooLarge(f"The file is too large. {LIMITS_TEXT}")
    return bytes(data)


def _applicant_household(principal: Principal) -> UUID:
    if principal.role is not Role.applicant or principal.household_id is None:
        raise Forbidden()
    return principal.household_id


async def _require_own_or_new_case(
    case_id: UUID, household_id: UUID, documents: DocumentRepository
) -> None:
    owner = await documents.owning_household(case_id)
    if owner is not None and owner != household_id:
        log.info("documents.access_refused", reason_code="wrong_case")
        raise Forbidden()


async def create_pay_stub(
    case_id: UUID,
    body: PayStubUpload,
    principal: Principal,
    *,
    documents: DocumentRepository,
    clock: Clock,
) -> DocumentCreated:
    household_id = _applicant_household(principal)  # never from the path or body (PL-6)
    bind_context(case_id=str(case_id))
    await _require_own_or_new_case(case_id, household_id, documents)
    scope = Scope.for_principal(principal)
    if await documents.count_documents(scope, case_id) >= MAX_UPLOADS:
        message = f"A case can have at most {MAX_UPLOADS} files."
        raise InvalidInput(
            message, fields=[FieldError(field="documents", message=message, type="too_many")]
        )
    document_id = uuid4()
    now = clock.now()
    document = StoredDocument(
        document_id=document_id,
        case_id=case_id,
        household_id=household_id,
        content_type=body.content_type,
        size_bytes=body.size_bytes,
        created_at=now,
    )
    details = PayStubDetails(
        document_id=document_id,
        case_id=case_id,
        household_id=household_id,
        employee_name=body.employee_name,
        employer=body.employer,
        frequency=body.frequency,
        gross_pay=body.gross_pay,
        pay_date=body.pay_date,
    )
    await documents.insert_stub(scope, document, details)
    log.info("documents.stub_recorded")
    return DocumentCreated(document_id=document_id)


def _check_type(content_type: str | None, data: bytes) -> str:
    media = (content_type or "").split(";")[0].strip().lower()
    if media not in ALLOWED_TYPES or not any(data.startswith(m) for m in MAGIC[media]):
        raise UnsupportedMediaType(f"The file is not a PDF, PNG or JPEG. {LIMITS_TEXT}")
    return media


async def upload_content(
    case_id: UUID,
    document_id: UUID,
    content_type: str | None,
    data: bytes,
    principal: Principal,
    *,
    documents: DocumentRepository,
    blobs: BlobStore,
    clock: Clock,
) -> None:
    household_id = _applicant_household(principal)
    bind_context(case_id=str(case_id))
    await _require_own_or_new_case(case_id, household_id, documents)
    scope = Scope.for_principal(principal)
    document = await documents.find_document(scope, case_id, document_id)
    if document is None:
        raise NotFound()
    if len(data) > MAX_UPLOAD_BYTES:
        raise PayloadTooLarge(f"The file is too large. {LIMITS_TEXT}")
    media = _check_type(content_type, data)
    if media != document.content_type:
        raise UnsupportedMediaType(f"The file type differs from the one recorded. {LIMITS_TEXT}")
    key = blob_key(household_id, document_id)
    await anyio.to_thread.run_sync(
        blobs.put, key, data
    )  # putting the same bytes again changes nothing
    await documents.mark_content(
        scope,
        document_id,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        blob_key=key,
        at=clock.now(),
    )
    log.info("documents.content_stored", size_bytes=len(data))


async def get_case_verification(
    case_id: UUID,
    principal: Principal,
    *,
    cases: CaseFinder,
    documents: DocumentRepository,
) -> CaseVerification:
    bind_context(case_id=str(case_id))
    scope = Scope.for_principal(principal)
    if await cases.find_case(scope, case_id) is None:
        raise NotFound()
    rows = await documents.list_documents(scope, case_id)
    result = await documents.find_result(scope, case_id)
    log.info("documents.verification_read", documents=len(rows), checked=result is not None)
    return CaseVerification(
        documents=[
            DocumentView(
                document_id=doc.document_id,
                content_type=doc.content_type,
                uploaded_at=doc.content_uploaded_at or doc.created_at,
                details=details,
            )
            for doc, details in rows
        ],
        result=result,
    )
