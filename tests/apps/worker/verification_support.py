"""Synthetic data and in-memory stand-ins for the 002 worker tests (KD-13, BA-11).

Everything here is invented: one household, one member with one job, one pay stub.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from pymongo import AsyncMongoClient

from apps.worker.repositories.case_members import CaseJob, CaseMember
from apps.worker.repositories.documents import money_to_bson, to_bson
from packages.domain.auth import Role
from packages.domain.case import PayFrequency, Relationship
from packages.domain.household import HouseholdMember, Job
from packages.domain.review import ActionOutcome, ActionType, CaseworkerAction, CorrectedValue
from packages.domain.verification import (
    Correction,
    IdentityMatch,
    PayStubDetails,
    ReferenceAnswer,
    ReferenceJob,
    ReferenceLookup,
    StubAssignment,
    VerificationResult,
)
from packages.platform.scope import Scope

HOUSEHOLD = UUID("00000000-0000-4000-8000-0000000000a1")
CASE = UUID("00000000-0000-4000-8000-0000000000c1")
MEMBER = UUID("00000000-0000-4000-8000-0000000000b1")
JOB = UUID("00000000-0000-4000-8000-0000000000d1")
DOCUMENT = UUID("00000000-0000-4000-8000-0000000000e1")
REVIEWER = UUID("00000000-0000-4000-8000-0000000000f1")
AS_OF = date(2026, 3, 17)
EMPLOYER = "Acme Synthetic Foods"
GIVEN, FAMILY, DOB = "Ana", "Rivera", date(1990, 5, 4)
SUBMITTED = datetime(2026, 3, 18, 15, 0, tzinfo=UTC)
# As the API's clock gives it: microseconds, which BSON cuts to milliseconds (ADR-002-06).
SUBMITTED_MICROS = datetime(2026, 3, 18, 15, 0, 0, 123456, tzinfo=UTC)
SCOPE = Scope.for_worker(CASE, HOUSEHOLD)


def case_member(*, declared: str = "2000.00") -> CaseMember:
    return CaseMember(
        member_id=MEMBER,
        given_name=GIVEN,
        family_name=FAMILY,
        date_of_birth=DOB,
        zero_income=False,
        jobs=[CaseJob(JOB, EMPLOYER, Decimal(declared), PayFrequency.monthly)],
    )


def stub_details(
    *, gross: str = "3000.00", corrections: list[Correction] | None = None
) -> PayStubDetails:
    return PayStubDetails(
        document_id=DOCUMENT,
        case_id=CASE,
        household_id=HOUSEHOLD,
        employee_name=f"{GIVEN} {FAMILY}",
        employer=EMPLOYER,
        frequency=PayFrequency.monthly,
        gross_pay=Decimal(gross),
        pay_date=date(2026, 3, 10),
        corrections=corrections or [],
    )


def reference_answer(*, identity: IdentityMatch = IdentityMatch.match) -> ReferenceAnswer:
    jobs = [ReferenceJob(employer=EMPLOYER, monthly_gross=Decimal("2000.00"))]
    return ReferenceAnswer(member_id=MEMBER, identity=identity, jobs=jobs)


def action(
    kind: ActionType,
    *,
    reason: str | None = "Checked the stub against the employer letter",
    corrected_value: CorrectedValue | None = None,
    finding_id: UUID | None = None,
    action_id: UUID | None = None,
) -> CaseworkerAction:
    return CaseworkerAction(
        action_id=action_id or uuid4(),
        task_id=uuid4(),
        action=kind,
        reviewer_id=REVIEWER,
        reviewer_role=Role.caseworker,
        reason=reason,
        corrected_value=corrected_value,
        finding_id=finding_id,
        submitted_at=SUBMITTED,
        household_id=HOUSEHOLD,
        case_id=CASE,
        outcome=ActionOutcome.applied,
        recorded_at=SUBMITTED,
    )


def correct_gross(value: str, *, document_id: UUID = DOCUMENT) -> CaseworkerAction:
    ref = CorrectedValue(field_ref=f"pay_stub:{document_id}:gross_pay", value=value)
    return action(ActionType.correct_value, corrected_value=ref)


class MemoryMembers:
    def __init__(self, members: list[CaseMember] | None = None) -> None:
        self.members = members if members is not None else [case_member()]
        self.calls = 0

    async def for_case(self, scope: Scope, as_of_date: date) -> list[CaseMember]:
        self.calls += 1
        return self.members


class MemoryReferences:
    def __init__(self, answer: ReferenceAnswer | None = None, *, error: Exception | None = None):
        self.answer = answer or reference_answer()
        self.error = error
        self.lookups: list[ReferenceLookup] = []

    async def lookup(self, scope: Scope, request: ReferenceLookup) -> ReferenceAnswer:
        if self.error is not None:
            raise self.error
        self.lookups.append(request)
        return self.answer


class MemoryStore:
    """An in-memory `PayStubStore`: corrections are appended, the result is replaced."""

    def __init__(self, details: list[PayStubDetails] | None = None) -> None:
        self.rows = details if details is not None else [stub_details()]
        self.result: VerificationResult | None = None
        self.as_of: date | None = None
        self.saves = 0
        self.assignments: list[StubAssignment] | None = None

    async def details(self, scope: Scope) -> list[PayStubDetails]:
        return list(self.rows)

    async def stored_result(self, scope: Scope) -> VerificationResult | None:
        return self.result

    async def stored_as_of_date(self, scope: Scope) -> date | None:
        return self.as_of

    async def stored_assignments(self, scope: Scope) -> list[StubAssignment] | None:
        return self.assignments

    async def save_result(
        self,
        scope: Scope,
        result: VerificationResult,
        as_of_date: date,
        checked_at: datetime,
        assignments: list[StubAssignment],
    ) -> None:
        self.saves += 1
        self.result, self.as_of, self.assignments = result, as_of_date, assignments

    async def append_correction(
        self, scope: Scope, document_id: UUID, correction: Correction
    ) -> None:
        self.rows = [
            r.model_copy(update={"corrections": [*r.corrections, correction]})
            if r.document_id == document_id
            else r
            for r in self.rows
        ]


async def seed_case(owner_url: str, db_name: str, *, reference_dob: date = DOB) -> None:
    """Insert the household member, the reference record and the stub as the data lane's seeds
    and the API would. Uses the owner connection: the worker's user only reads these."""
    client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
        owner_url, uuidRepresentation="standard", tz_aware=True, tzinfo=UTC
    )
    try:
        db = client[db_name]
        for name in ("household_members", "reference_records", "pay_stub_details"):
            await db[name].delete_many({"household_id": HOUSEHOLD})
        await db["verification_results"].delete_many({"case_id": CASE})
        member = HouseholdMember(
            member_id=MEMBER,
            household_id=HOUSEHOLD,
            given_name=GIVEN,
            family_name=FAMILY,
            date_of_birth=DOB,
            relationship=Relationship.self,
            address_state="IL",
            citizenship_attested=True,
            medicare_entitled=False,
            declared_zero_income=False,
            jobs=[
                Job(
                    job_id=JOB,
                    employer=EMPLOYER,
                    declared_frequency=PayFrequency.monthly,
                    declared_gross=Decimal("2000.00"),
                )
            ],
            coverage_year=AS_OF.year,
        )
        document = money_to_bson(member.model_dump())
        document["_id"] = document.pop("member_id")
        await db["household_members"].insert_one({**document, "synthetic": True})
        await db["reference_records"].insert_one(
            {
                "_id": uuid4(),  # 0007's validator requires a UUID _id, as the seeds give
                "household_id": HOUSEHOLD,
                "member_id": MEMBER,
                "given_name": GIVEN,
                "family_name": FAMILY,
                "date_of_birth": to_bson(reference_dob),
                "jobs": [{"employer": EMPLOYER, "monthly_gross": "2000.00"}],
                "scenario_tags": [],
                "synthetic": True,
            }
        )
        details = stub_details()
        await db["pay_stub_details"].insert_one(
            # _id is the document ID, as the upload writes it (apps/api/repositories/documents.py)
            {"_id": details.document_id, **money_to_bson(details.model_dump()), "synthetic": True}
        )
    finally:
        await client.close()
