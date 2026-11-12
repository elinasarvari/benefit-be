"""The pay stub check (002, ADR-002-02, ADR-002-05): pure and deterministic (KD-1, FR-002-23).

Compares each declared job's monthly income with the latest matching pay stub, matches stubs to
members by case-folded employee name and to jobs by employer, finds missing documents and
identity mismatches. It decides nothing about eligibility (FR-002-08, KD-2): the result holds only
findings and missing items. The monthly income formula is FR-003-18 (`monthly_income_from_stub`).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID

from packages.domain.case import PayFrequency
from packages.domain.household import PayStubFact
from packages.domain.verification import (
    Dismissal,
    Finding,
    FindingCause,
    IdentityMatch,
    MissingDocument,
    MissingDocumentKind,
    PayStubDetails,
    ReferenceAnswer,
    StubAssignment,
    VerificationResult,
)
from packages.rules.income import monthly_income_from_stub

STALE_STUB_DAYS = 45  # FR-002-15
_FINDING_NAMESPACE = uuid.UUID("5d2c1b8e-6f0a-4e0b-9a43-2b7a1c0f6e11")
_HUNDRED = Decimal(100)


@dataclass(frozen=True)
class DeclaredJob:
    job_id: UUID
    employer: str
    declared_monthly: Decimal


@dataclass(frozen=True)
class DeclaredMember:
    member_id: UUID
    full_name: str
    zero_income: bool = False
    jobs: Sequence[DeclaredJob] = ()


@dataclass(frozen=True)
class CheckInput:
    case_id: UUID
    as_of_date: date
    members: Sequence[DeclaredMember]
    stubs: Sequence[PayStubDetails]
    references: Mapping[UUID, ReferenceAnswer] = field(default_factory=dict)
    dismissed: Mapping[UUID, Dismissal] = field(default_factory=dict)


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def _finding_id(
    case_id: UUID, cause: FindingCause, member: UUID | None, job: UUID | None, doc: UUID | None
) -> UUID:
    return uuid.uuid5(_FINDING_NAMESPACE, f"{case_id}:{cause.value}:{member}:{job}:{doc}")


def _fact(stub: PayStubDetails) -> PayStubFact:
    return PayStubFact(
        document_id=stub.document_id,
        pay_date=stub.pay_date,
        frequency=stub.frequency,
        gross_pay=stub.gross_pay,
    )


def _conversion(stub: PayStubDetails, monthly: Decimal) -> str:
    periods = {"weekly": 52, "biweekly": 26, "semimonthly": 24, "monthly": 12}[stub.frequency.value]
    if periods == 12:
        return f"monthly {stub.gross_pay:.2f} = {monthly:.2f}"
    return f"{stub.frequency.value} {stub.gross_pay:.2f} x {periods} / 12 = {monthly:.2f}"


def _latest(stubs: Sequence[PayStubDetails]) -> PayStubDetails:
    return max(stubs, key=lambda s: (s.pay_date, str(s.document_id)))


def assign_stubs(
    members: Sequence[DeclaredMember], stubs: Sequence[PayStubDetails]
) -> tuple[dict[tuple[UUID, UUID], list[PayStubDetails]], list[PayStubDetails]]:
    """The one rule for who and which job a stub belongs to (ADR-002-04, ADR-002-05).

    Returns the stubs used per (member, job) and the stubs of nobody in the household. The check
    and the determination's facts both read this assignment, so they never disagree.
    """
    by_name: dict[str, list[DeclaredMember]] = {}
    for member in members:
        by_name.setdefault(_norm(member.full_name), []).append(member)
    used: dict[tuple[UUID, UUID], list[PayStubDetails]] = {}
    different_person: list[PayStubDetails] = []
    for stub in stubs:
        candidates = by_name.get(_norm(stub.employee_name))
        if not candidates:
            different_person.append(stub)
            continue
        employer = _norm(stub.employer)
        monthly = monthly_income_from_stub(_fact(stub))
        for member in candidates:
            same_employer = [j for j in member.jobs if _norm(j.employer) == employer]
            if same_employer:
                # Two jobs with one employer: the stub belongs to the one it is closest to, so
                # the pick never depends on input order (FR-002-23); ties go to the lower ID.
                job = min(
                    same_employer,
                    key=lambda j: (abs(j.declared_monthly - monthly), str(j.job_id)),
                )
                used.setdefault((member.member_id, job.job_id), []).append(stub)
                break
        # a stub of a known member whose employer matches no job is not used (ADR-002-05)
    return used, different_person


def declared_monthly(gross: Decimal, frequency: PayFrequency, as_of_date: date) -> Decimal:
    """The declared pay as a monthly amount, converted like a stub's (FR-003-18)."""
    fact = PayStubFact(
        document_id=UUID(int=0), pay_date=as_of_date, frequency=frequency, gross_pay=gross
    )
    return monthly_income_from_stub(fact)


def stub_assignments(
    members: Sequence[DeclaredMember], stubs: Sequence[PayStubDetails]
) -> list[StubAssignment]:
    """`assign_stubs` as sorted records the facts builder stores and reads back."""
    used, _ = assign_stubs(members, stubs)
    return sorted(
        (
            StubAssignment(member_id=member_id, job_id=job_id, document_id=stub.document_id)
            for (member_id, job_id), matched in used.items()
            for stub in matched
        ),
        key=lambda a: (str(a.member_id), str(a.job_id), str(a.document_id)),
    )


def check_pay_stubs(data: CheckInput, *, threshold_percent: Decimal) -> VerificationResult:
    threshold = Decimal(threshold_percent)
    findings: list[Finding] = []

    def add(
        cause: FindingCause,
        *,
        member_id: UUID | None = None,
        job_id: UUID | None = None,
        document_id: UUID | None = None,
        identity: IdentityMatch | None = None,
        declared_monthly: Decimal | None = None,
        documented_monthly: Decimal | None = None,
        conversion: str | None = None,
    ) -> None:
        fid = _finding_id(data.case_id, cause, member_id, job_id, document_id)
        findings.append(
            Finding(
                finding_id=fid,
                cause=cause,
                member_id=member_id,
                job_id=job_id,
                document_id=document_id,
                identity=identity,
                declared_monthly=declared_monthly,
                documented_monthly=documented_monthly,
                conversion=conversion,
                dismissed=data.dismissed.get(fid),
            )
        )

    used, different_person = assign_stubs(data.members, data.stubs)
    for stub in different_person:
        add(FindingCause.different_person, document_id=stub.document_id)

    missing: list[MissingDocument] = []
    for member in data.members:
        for job in member.jobs:
            stubs = used.get((member.member_id, job.job_id), [])
            if not stubs:
                missing.append(
                    MissingDocument(
                        member_id=member.member_id,
                        job_id=job.job_id,
                        kind=MissingDocumentKind.no_stub,
                    )
                )
                continue
            latest = _latest(stubs)
            seen: dict[tuple[date, str], PayStubDetails] = {}
            conflicted_dates: set[date] = set()
            for stub in sorted(stubs, key=lambda s: str(s.document_id)):
                key = (stub.pay_date, stub.frequency.value)
                first = seen.setdefault(key, stub)
                if first.gross_pay != stub.gross_pay and stub.pay_date not in conflicted_dates:
                    conflicted_dates.add(stub.pay_date)
                    add(
                        FindingCause.conflicting_stubs,
                        member_id=member.member_id,
                        job_id=job.job_id,
                        document_id=first.document_id,
                    )
            if (data.as_of_date - latest.pay_date).days > STALE_STUB_DAYS:
                missing.append(
                    MissingDocument(
                        member_id=member.member_id,
                        job_id=job.job_id,
                        kind=MissingDocumentKind.stub_too_old,
                    )
                )
                continue
            if latest.pay_date in conflicted_dates:
                continue
            documented = monthly_income_from_stub(_fact(latest))
            if abs(documented - job.declared_monthly) * _HUNDRED > threshold * job.declared_monthly:
                add(
                    FindingCause.income_mismatch,
                    member_id=member.member_id,
                    job_id=job.job_id,
                    document_id=latest.document_id,
                    declared_monthly=job.declared_monthly,
                    documented_monthly=documented,
                    conversion=_conversion(latest, documented),
                )

    for member in data.members:
        answer = data.references.get(member.member_id)
        identity = IdentityMatch.no_record if answer is None else answer.identity
        if identity is not IdentityMatch.match:
            add(
                FindingCause.identity_mismatch,
                member_id=member.member_id,
                identity=identity,
            )

    findings.sort(
        key=lambda f: (f.cause.value, str(f.member_id), str(f.job_id), str(f.document_id))
    )
    missing.sort(key=lambda m: (str(m.member_id), str(m.job_id), m.kind.value))
    identity_open = any(
        f.cause is FindingCause.identity_mismatch and f.dismissed is None for f in findings
    )
    return VerificationResult(
        complete=not missing,
        needs_review=any(f.dismissed is None for f in findings),
        case_id=data.case_id,
        identity_verified=not identity_open,
        findings=findings,
        missing_items=missing,
    )
