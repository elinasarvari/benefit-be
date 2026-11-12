"""Entered pay stubs, stored check results, and the `VerifiedFacts` built from them (ADR-002-04).

`pay_stub_details` (by case) holds what the applicant typed plus the caseworker's corrections;
`verification_results` holds one result per case (`_id` = `case_id`, replaced on every run, so a
retried activity stores one result, TW-11). Every query goes through the worker scope (R16).
`EnteredPayStubs` replaces `SeededVerifications` for a case that has entered stubs.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Protocol
from uuid import UUID

from apps.worker.repositories.case_members import CaseMembers
from apps.worker.repositories.documents import as_date, from_bson, money_to_bson, to_bson
from apps.worker.repositories.verified_facts import VerifiedFacts
from packages.domain.household import JobIncome, PayStubFact, VerifiedMemberFacts
from packages.domain.verification import (
    Correction,
    FindingCause,
    PayStubDetails,
    StubAssignment,
    VerificationResult,
    effective,
)
from packages.platform.clock import Clock, today
from packages.platform.errors import Forbidden
from packages.platform.mongo import Client
from packages.platform.scope import Scope, find_many, scoped_filter

PAY_STUB_DETAILS = "pay_stub_details"
VERIFICATION_RESULTS = "verification_results"
DETAIL_FIELDS = {"_id": 0, **{name: 1 for name in PayStubDetails.model_fields}}
RESULT_FIELDS = {
    "_id": 0,
    "as_of_date": 1,
    "stub_assignments": 1,
    **{name: 1 for name in VerificationResult.model_fields},
}


def detail_from_document(document: dict[str, Any]) -> PayStubDetails:
    fields = from_bson(document)
    fields["pay_date"] = as_date(fields["pay_date"])
    return PayStubDetails.model_validate(fields)


def result_from_document(document: dict[str, Any]) -> VerificationResult:
    fields = from_bson({k: v for k, v in document.items() if k in VerificationResult.model_fields})
    return VerificationResult.model_validate(fields)


class PayStubStore(Protocol):
    async def details(self, scope: Scope) -> list[PayStubDetails]:
        """The case's stored details, raw (corrections listed, not applied)."""
        ...

    async def stored_result(self, scope: Scope) -> VerificationResult | None: ...

    async def save_result(
        self,
        scope: Scope,
        result: VerificationResult,
        as_of_date: date,
        checked_at: datetime,
        assignments: list[StubAssignment],
    ) -> None:
        """Store the case's one result and the check's stub assignment, replacing the previous
        run's (idempotent)."""
        ...

    async def stored_assignments(self, scope: Scope) -> list[StubAssignment] | None:
        """The stub to job assignment of the last run, None before any run."""
        ...

    async def stored_as_of_date(self, scope: Scope) -> date | None: ...

    async def append_correction(
        self, scope: Scope, document_id: UUID, correction: Correction
    ) -> None: ...


class MongoPayStubs:
    def __init__(self, client: Client, db_name: str) -> None:
        self._db = client[db_name]

    async def details(self, scope: Scope) -> list[PayStubDetails]:
        documents = await find_many(self._db, PAY_STUB_DETAILS, scope, projection=DETAIL_FIELDS)
        return [detail_from_document(document) for document in documents]

    async def stored_result(self, scope: Scope) -> VerificationResult | None:
        document = await self._result_document(scope)
        return result_from_document(document) if document else None

    async def stored_as_of_date(self, scope: Scope) -> date | None:
        document = await self._result_document(scope)
        return as_date(document["as_of_date"]) if document and "as_of_date" in document else None

    async def stored_assignments(self, scope: Scope) -> list[StubAssignment] | None:
        document = await self._result_document(scope)
        if document is None:
            return None
        return [
            StubAssignment.model_validate(from_bson(a))
            for a in document.get("stub_assignments", [])
        ]

    async def save_result(
        self,
        scope: Scope,
        result: VerificationResult,
        as_of_date: date,
        checked_at: datetime,
        assignments: list[StubAssignment],
    ) -> None:
        if scope.case_id is None or scope.household_id is None:
            raise Forbidden()
        document: dict[str, Any] = money_to_bson(result.model_dump())
        document.update(
            {
                "_id": scope.case_id,
                "case_id": scope.case_id,
                "household_id": scope.household_id,
                "as_of_date": to_bson(as_of_date),
                "stub_assignments": [to_bson(a.model_dump()) for a in assignments],
                "checked_at": checked_at,
                "synthetic": True,
            }
        )
        query = scoped_filter(scope, VERIFICATION_RESULTS, {"_id": scope.case_id})
        await self._db[VERIFICATION_RESULTS].replace_one(query, document, upsert=True)

    async def append_correction(
        self, scope: Scope, document_id: UUID, correction: Correction
    ) -> None:
        query = scoped_filter(scope, PAY_STUB_DETAILS, {"document_id": document_id})
        await self._db[PAY_STUB_DETAILS].update_one(
            query, {"$push": {"corrections": to_bson(correction.model_dump())}}
        )

    async def _result_document(self, scope: Scope) -> dict[str, Any] | None:
        documents = await find_many(
            self._db, VERIFICATION_RESULTS, scope, projection=RESULT_FIELDS, limit=1
        )
        return documents[0] if documents else None


class EnteredPayStubs:
    """`VerifiedFacts` for a case with entered stubs; the seeded verifications otherwise."""

    def __init__(
        self, store: PayStubStore, members: CaseMembers, seeded: VerifiedFacts, clock: Clock
    ) -> None:
        self._store = store
        self._members = members
        self._seeded = seeded
        self._clock = clock

    async def for_household(self, scope: Scope) -> list[VerifiedMemberFacts]:
        raw = await self._store.details(scope)
        seeded = await self._seeded.for_household(scope)
        if not raw or scope.household_id is None:
            return seeded
        stubs = {details.document_id: effective(details) for details in raw}
        seeded_by_member = {facts.member_id: facts for facts in seeded}
        result = await self._store.stored_result(scope)
        assignments = await self._store.stored_assignments(scope)
        if assignments is None:
            return seeded  # the check has not run: nothing says which job a stub belongs to
        by_job: dict[tuple[UUID, UUID], list[PayStubDetails]] = {}
        for assignment in assignments:
            stub = stubs.get(assignment.document_id)
            if stub is not None:
                by_job.setdefault((assignment.member_id, assignment.job_id), []).append(stub)
        as_of = await self._store.stored_as_of_date(scope) or today(self._clock)
        facts: list[VerifiedMemberFacts] = []
        for member in await self._members.for_case(scope, as_of):
            jobs = []
            for job in member.jobs:
                # the check's own pick of the latest stub (ties: higher document id)
                latest = max(
                    by_job.get((member.member_id, job.job_id), []),
                    key=lambda s: (s.pay_date, str(s.document_id)),
                    default=None,
                )
                jobs.append(
                    JobIncome(
                        job_id=job.job_id,
                        stubs=[]
                        if latest is None
                        else [
                            PayStubFact(
                                document_id=latest.document_id,
                                pay_date=latest.pay_date,
                                frequency=latest.frequency,
                                gross_pay=latest.gross_pay,
                            )
                        ],
                    )
                )
            if result is None:
                previous = seeded_by_member.get(member.member_id)
                identity = previous.identity_verified if previous else None
            else:
                identity = not any(
                    f.dismissed is None
                    and f.cause is FindingCause.identity_mismatch
                    and f.member_id == member.member_id
                    for f in result.findings
                )
            facts.append(
                VerifiedMemberFacts(
                    household_id=scope.household_id,
                    member_id=member.member_id,
                    identity_verified=identity,
                    jobs=jobs,
                )
            )
        return facts
