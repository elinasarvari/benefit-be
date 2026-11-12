"""In-memory document repository and a harness for the 002 upload and verification routes."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from uuid import UUID

from apps.api.dependencies.documents import get_blob_store, get_document_repository
from apps.api.repositories.documents import StoredDocument
from packages.domain.auth import Role
from packages.domain.verification import PayStubDetails, VerificationResult
from packages.platform.blobs import MemoryBlobStore
from packages.platform.errors import Forbidden
from packages.platform.scope import Scope
from packages.platform.settings import Settings
from tests.apps.api.case_fakes import CaseApiHarness
from tests.apps.api.fakes import HOUSEHOLD

PDF = b"%PDF-1.4 synthetic pay stub"
PNG = b"\x89PNG\r\n\x1a\n synthetic"
JPEG = b"\xff\xd8\xff\xe0 synthetic"
OTHER_HOUSEHOLD = uuid.uuid4()

STUB_BODY = {
    "employee_name": "Sample Synthetic Person",
    "employer": "Synthetic Employer Inc",
    "frequency": "weekly",
    "gross_pay": "300.00",
    "pay_date": "2026-03-01",
    "content_type": "application/pdf",
    "size_bytes": len(PDF),
}


def _visible(scope: Scope, household_id: UUID) -> bool:
    return scope.role is not Role.applicant or scope.household_id == household_id


class FakeDocumentRepository:
    def __init__(self, cases: CaseApiHarness) -> None:
        self._cases = cases
        self.documents: dict[UUID, StoredDocument] = {}
        self.details: dict[UUID, PayStubDetails] = {}
        self.results: dict[UUID, VerificationResult] = {}
        self.calls: list[tuple[str, Scope]] = []

    async def owning_household(self, case_id: UUID) -> UUID | None:
        case = self._cases.repo.cases.get(case_id)
        if case is not None:
            return case.household_id
        return next((d.household_id for d in self.documents.values() if d.case_id == case_id), None)

    async def count_documents(self, scope: Scope, case_id: UUID) -> int:
        self.calls.append(("count_documents", scope))
        return sum(
            1
            for d in self.documents.values()
            if d.case_id == case_id and _visible(scope, d.household_id)
        )

    async def insert_stub(
        self, scope: Scope, document: StoredDocument, details: PayStubDetails
    ) -> None:
        if not _visible(scope, document.household_id):
            raise Forbidden()
        self.documents[document.document_id] = document
        self.details[document.document_id] = details

    async def find_document(
        self, scope: Scope, case_id: UUID, document_id: UUID
    ) -> StoredDocument | None:
        self.calls.append(("find_document", scope))
        doc = self.documents.get(document_id)
        if doc is None or doc.case_id != case_id or not _visible(scope, doc.household_id):
            return None
        return doc

    async def mark_content(
        self,
        scope: Scope,
        document_id: UUID,
        *,
        size_bytes: int,
        sha256: str,
        blob_key: str,
        at: datetime,
    ) -> None:
        doc = self.documents[document_id]
        self.documents[document_id] = StoredDocument(
            document_id=doc.document_id,
            case_id=doc.case_id,
            household_id=doc.household_id,
            content_type=doc.content_type,
            size_bytes=size_bytes,
            created_at=doc.created_at,
            sha256=sha256,
            blob_key=blob_key,
            content_uploaded_at=at,
        )

    async def list_documents(
        self, scope: Scope, case_id: UUID
    ) -> list[tuple[StoredDocument, PayStubDetails]]:
        self.calls.append(("list_documents", scope))
        rows = [
            d
            for d in self.documents.values()
            if d.case_id == case_id and _visible(scope, d.household_id)
        ]
        rows.sort(key=lambda d: d.created_at)
        return [(d, self.details[d.document_id]) for d in rows]

    async def find_result(self, scope: Scope, case_id: UUID) -> VerificationResult | None:
        self.calls.append(("find_result", scope))
        return self.results.get(case_id)


class DocumentsHarness(CaseApiHarness):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.documents = FakeDocumentRepository(self)
        self.blobs = MemoryBlobStore()
        app = self.client.app
        app.dependency_overrides[get_document_repository] = lambda: self.documents  # type: ignore[attr-defined]
        app.dependency_overrides[get_blob_store] = lambda: self.blobs  # type: ignore[attr-defined]

    def create_stub(self, case_id: UUID, **overrides: object) -> Any:
        return self.client.post(
            f"/api/v1/cases/{case_id}/documents", json={**STUB_BODY, **overrides}
        )

    def put_content(
        self, case_id: UUID, document_id: object, data: bytes, content_type: str = "application/pdf"
    ) -> Any:
        return self.client.put(
            f"/api/v1/cases/{case_id}/documents/{document_id}/content",
            content=data,
            headers={"Content-Type": content_type},
        )


__all__ = ["HOUSEHOLD", "OTHER_HOUSEHOLD", "DocumentsHarness", "FakeDocumentRepository"]
