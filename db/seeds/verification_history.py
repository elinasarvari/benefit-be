"""The pay stub check for seeded history cases (002, FR-002-18; TC-69, TC-70).

Each history case gets its household's pay stubs for that year as `documents` and
`pay_stub_details`, and the result of the real pure check (`packages.rules.check_pay_stubs`) as
`verification_results`, in the shapes the API and worker store. So the caseworker's documents
section has stubs, entered details and findings to show before 011 runs the check from the case
workflow. The files themselves aren't stored (`blob_key` stays null). All data is synthetic (PR-3).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from db.seeds.households import HouseholdYear, StubDocument
from db.seeds.ids import seeded_id
from db.seeds.records import convert
from packages.domain.household import HouseholdMember
from packages.domain.verification import (
    IdentityMatch,
    PayStubDetails,
    ReferenceAnswer,
    ReferenceJob,
)
from packages.rules.verification import (
    CheckInput,
    DeclaredJob,
    DeclaredMember,
    check_pay_stubs,
    declared_monthly,
    stub_assignments,
)

THRESHOLD_PERCENT = Decimal(10)  # the settings default (INCOME_MISMATCH_THRESHOLD_PERCENT)
COLLECTIONS = ("documents", "pay_stub_details", "verification_results")


def reference_answers(reference_documents: list[dict[str, Any]]) -> dict[UUID, dict[str, Any]]:
    """The seeded reference record of each member, by member ID."""
    return {doc["member_id"]: doc for doc in reference_documents}


def _answer(member: HouseholdMember, record: Mapping[str, Any] | None) -> ReferenceAnswer:
    if record is None:
        return ReferenceAnswer(member_id=member.member_id, identity=IdentityMatch.no_record)
    birth = record["date_of_birth"]
    same = (
        record["given_name"].casefold() == member.given_name.casefold()
        and record["family_name"].casefold() == member.family_name.casefold()
        and (birth.date() if isinstance(birth, datetime) else birth) == member.date_of_birth
    )
    return ReferenceAnswer(
        member_id=member.member_id,
        identity=IdentityMatch.match if same else IdentityMatch.no_match,
        jobs=[
            ReferenceJob(employer=j["employer"], monthly_gross=Decimal(str(j["monthly_gross"])))
            for j in record["jobs"]
        ],
    )


def document_id_for(case_id: UUID, stub: StubDocument) -> UUID:
    """A history case's own copy of a seeded stub. A household's prior and current cases can fall
    in the same coverage year and share its stubs, so the ID comes from the case and the stub."""
    return seeded_id(f"history-document-{case_id}-{stub.document_id}")


def _details(stub: StubDocument, case_id: UUID) -> PayStubDetails:
    return PayStubDetails(
        document_id=document_id_for(case_id, stub),
        case_id=case_id,
        household_id=stub.household_id,
        member_id=stub.member_id,
        employee_name=stub.employee_name,
        employer=stub.employer,
        frequency=stub.frequency,
        gross_pay=stub.gross_pay,
        pay_date=stub.pay_date,
    )


def case_verification(
    year: HouseholdYear,
    *,
    case_id: UUID,
    household_id: UUID,
    as_of_date: date,
    checked_at: datetime,
    references: Mapping[UUID, Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """The three collections' documents for one history case, checked as of its received date."""
    details = [_details(stub, case_id) for stub in year.stubs]
    members = [
        DeclaredMember(
            member_id=m.member_id,
            full_name=f"{m.given_name} {m.family_name}",
            zero_income=m.declared_zero_income,
            jobs=[
                DeclaredJob(
                    job_id=j.job_id,
                    employer=j.employer,
                    declared_monthly=declared_monthly(
                        j.declared_gross, j.declared_frequency, as_of_date
                    ),
                )
                for j in m.jobs
            ],
        )
        for m in year.members
    ]
    data = CheckInput(
        case_id=case_id,
        as_of_date=as_of_date,
        members=members,
        stubs=details,
        references={m.member_id: _answer(m, references.get(m.member_id)) for m in year.members},
    )
    result = check_pay_stubs(data, threshold_percent=THRESHOLD_PERCENT)
    documents = [
        {
            "_id": document_id_for(case_id, stub),
            "case_id": case_id,
            "household_id": household_id,
            "doc_type": "pay_stub",
            "content_type": "image/png" if stub.poor_quality else "application/pdf",
            "size_bytes": 0,
            "sha256": None,
            "blob_key": None,
            "created_at": checked_at,
            "content_uploaded_at": None,
            "synthetic": True,
        }
        for stub in year.stubs
    ]
    pay_stub_details = [{"_id": d.document_id, **convert(d), "synthetic": True} for d in details]
    stored = convert(result)
    stored.update(
        {
            "_id": case_id,
            "case_id": case_id,
            "household_id": household_id,
            "as_of_date": convert(as_of_date),
            "stub_assignments": [convert(a) for a in stub_assignments(members, details)],
            "checked_at": checked_at,
            "synthetic": True,
        }
    )
    return {
        "documents": documents,
        "pay_stub_details": pay_stub_details,
        "verification_results": [stored],
    }
