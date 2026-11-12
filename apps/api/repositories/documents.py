"""Pay stub documents, their typed details and the stored check result (002, data-model.md).

`documents` and `pay_stub_details` are written here (the upload); `verification_results` is only
read (the worker writes it). Every read goes through `packages.platform.scope` (PL-6). Money is a
decimal string and dates are BSON dates at midnight UTC, the same shapes the worker reads
(`pay_stub_details`, ADR-002-04). Every row is `synthetic: true` (PR-3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID

from bson.decimal128 import Decimal128

from packages.domain.verification import PayStubDetails, VerificationResult
from packages.platform.mongo import Client
from packages.platform.scope import Scope, ScopeKind, scoped_filter

DOCUMENTS = "documents"
PAY_STUB_DETAILS = "pay_stub_details"
VERIFICATION_RESULTS = "verification_results"
CASES = "cases"
MAX_PER_CASE = 10  # the 10-file limit is checked in the service, reads here are bounded by it
DOC_TYPE = "pay_stub"
DOCUMENT_FIELDS = {
    name: 1
    for name in (
        "case_id",
        "household_id",
        "content_type",
        "size_bytes",
        "created_at",
        "sha256",
        "blob_key",
        "content_uploaded_at",
    )
}
DETAIL_FIELDS = {name: 1 for name in PayStubDetails.model_fields} | {"_id": 0}
RESULT_FIELDS = {name: 1 for name in VerificationResult.model_fields} | {"_id": 0}


@dataclass(frozen=True)
class StoredDocument:
    document_id: UUID
    case_id: UUID
    household_id: UUID
    content_type: str
    size_bytes: int
    created_at: datetime
    sha256: str | None = None
    blob_key: str | None = None
    content_uploaded_at: datetime | None = None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _number(value: Any) -> Any:
    return value.to_decimal() if isinstance(value, Decimal128) else value


def _day(value: Any) -> Any:
    return _aware(value).date() if isinstance(value, datetime) else value


def document_from_bson(doc: dict[str, Any]) -> StoredDocument:
    return StoredDocument(
        document_id=doc["_id"],
        case_id=doc["case_id"],
        household_id=doc["household_id"],
        content_type=doc["content_type"],
        size_bytes=doc["size_bytes"],
        created_at=_aware(doc["created_at"]),
        sha256=doc.get("sha256"),
        blob_key=doc.get("blob_key"),
        content_uploaded_at=(
            _aware(doc["content_uploaded_at"]) if doc.get("content_uploaded_at") else None
        ),
    )


def details_to_bson(details: PayStubDetails) -> dict[str, Any]:
    doc = details.model_dump(mode="python")
    doc["_id"] = details.document_id
    doc["gross_pay"] = str(details.gross_pay)
    doc["pay_date"] = datetime.combine(details.pay_date, time.min, tzinfo=UTC)
    doc["synthetic"] = True
    return doc


def details_from_bson(doc: dict[str, Any]) -> PayStubDetails:
    data = {name: doc[name] for name in PayStubDetails.model_fields if name in doc}
    data["gross_pay"] = Decimal(str(_number(data["gross_pay"])))
    data["pay_date"] = _day(data["pay_date"])
    return PayStubDetails.model_validate(data)


def result_from_bson(doc: dict[str, Any]) -> VerificationResult:
    data = {name: doc[name] for name in VerificationResult.model_fields if name in doc}
    findings = []
    for finding in data.get("findings", []):
        finding = dict(finding)
        for name in ("declared_monthly", "documented_monthly"):
            if finding.get(name) is not None:
                finding[name] = Decimal(str(_number(finding[name])))
        findings.append(finding)
    data["findings"] = findings
    return VerificationResult.model_validate(data)


class MongoDocumentRepository:
    def __init__(self, client: Client, db_name: str) -> None:
        self._client = client
        self._db_name = db_name

    @property
    def _db(self) -> Any:
        return self._client[self._db_name]

    async def owning_household(self, case_id: UUID) -> UUID | None:
        """The household a case (or, before it exists, its first upload) belongs to.

        Only the household ID is read, to tell another household's case from one that does not
        exist yet (ADR-002-01). It is never returned to the caller."""
        scope = Scope(ScopeKind.system, case_id=case_id)
        for collection in (CASES, DOCUMENTS):
            doc = await self._db[collection].find_one(
                scoped_filter(scope, collection), {"household_id": 1}
            )
            if doc is not None:
                household: UUID = doc["household_id"]
                return household
        return None

    async def count_documents(self, scope: Scope, case_id: UUID) -> int:
        query = scoped_filter(scope, DOCUMENTS, {"case_id": case_id})
        return int(await self._db[DOCUMENTS].count_documents(query))

    async def insert_stub(
        self, scope: Scope, document: StoredDocument, details: PayStubDetails
    ) -> None:
        if scope.household_id != document.household_id:
            raise ValueError("document is outside the scope")
        row = {
            "_id": document.document_id,
            "case_id": document.case_id,
            "household_id": document.household_id,
            "doc_type": DOC_TYPE,
            "content_type": document.content_type,
            "size_bytes": document.size_bytes,
            "sha256": None,
            "blob_key": None,
            "created_at": document.created_at,
            "content_uploaded_at": None,
            "synthetic": True,
        }

        async def write(session: Any) -> None:
            # one transaction: a document row without its details would count toward the 10-file
            # limit but never be listed or checked
            await self._db[DOCUMENTS].insert_one(row, session=session)
            await self._db[PAY_STUB_DETAILS].insert_one(details_to_bson(details), session=session)

        async with self._client.start_session() as session:
            await session.with_transaction(write)

    async def find_document(
        self, scope: Scope, case_id: UUID, document_id: UUID
    ) -> StoredDocument | None:
        query = scoped_filter(scope, DOCUMENTS, {"_id": document_id, "case_id": case_id})
        doc = await self._db[DOCUMENTS].find_one(query, DOCUMENT_FIELDS)
        return document_from_bson(doc) if doc else None

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
        query = scoped_filter(scope, DOCUMENTS, {"_id": document_id})
        await self._db[DOCUMENTS].update_one(
            query,
            {
                "$set": {
                    "size_bytes": size_bytes,
                    "sha256": sha256,
                    "blob_key": blob_key,
                    "content_uploaded_at": at,
                }
            },
        )

    async def list_documents(
        self, scope: Scope, case_id: UUID
    ) -> list[tuple[StoredDocument, PayStubDetails]]:
        docs_cursor = (
            self._db[DOCUMENTS]
            .find(scoped_filter(scope, DOCUMENTS, {"case_id": case_id}), DOCUMENT_FIELDS)
            .sort([("created_at", 1), ("_id", 1)])
            .limit(MAX_PER_CASE)
        )
        documents = [document_from_bson(d) for d in await docs_cursor.to_list(MAX_PER_CASE)]
        details_cursor = self._db[PAY_STUB_DETAILS].find(
            scoped_filter(scope, PAY_STUB_DETAILS, {"case_id": case_id}), DETAIL_FIELDS
        )
        by_id = {
            d.document_id: d
            for d in [details_from_bson(x) for x in await details_cursor.to_list(MAX_PER_CASE)]
        }
        return [(doc, by_id[doc.document_id]) for doc in documents if doc.document_id in by_id]

    async def find_result(self, scope: Scope, case_id: UUID) -> VerificationResult | None:
        query = scoped_filter(scope, VERIFICATION_RESULTS, {"case_id": case_id})
        doc = await self._db[VERIFICATION_RESULTS].find_one(query, RESULT_FIELDS)
        return result_from_bson(doc) if doc else None
