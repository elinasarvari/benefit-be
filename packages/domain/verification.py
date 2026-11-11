"""Pay stub check contract (002, MVP): the verification result, pay stub details and the
reference lookup's answer. See specs/mvp-contracts.md section 2 in the metarepo.

Shared by Workflows (the check and the case workflow), Platform & UI (upload and the case page)
and Knowledge & data (the reference records). Pure models only (BA-6, BA-8).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from packages.domain.case import PayFrequency

TEXT_MAX_LENGTH = 100
REASON_MAX_LENGTH = 500


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FindingCause(StrEnum):
    """FR-002-16: the causes a "Verification review" task shows."""

    income_mismatch = "income_mismatch"
    different_person = "different_person"
    conflicting_stubs = "conflicting_stubs"
    identity_mismatch = "identity_mismatch"


class MissingDocumentKind(StrEnum):
    """FR-002-15: a declared job with no pay stub, or whose latest stub is too old."""

    no_stub = "no_stub"
    stub_too_old = "stub_too_old"


class IdentityMatch(StrEnum):
    match = "match"
    no_match = "no_match"
    no_record = "no_record"


class Dismissal(_Model):
    reviewer_id: UUID
    reason: str = Field(min_length=1, max_length=REASON_MAX_LENGTH)
    dismissed_at: datetime


class Finding(_Model):
    """One discrepancy (FR-002-03, FR-002-14, FR-002-24). `finding_id` is derived from the case,
    cause, member, job and document, so running the check again gives the same ID (FR-002-23)
    and two "different person" stubs (no member, no job) still get different IDs.
    `identity` is set for identity mismatches: `no_match` or `no_record` (FR-002-24)."""

    finding_id: UUID
    cause: FindingCause
    member_id: UUID | None = None
    job_id: UUID | None = None
    document_id: UUID | None = None
    identity: IdentityMatch | None = None
    declared_monthly: Decimal | None = None
    documented_monthly: Decimal | None = None
    conversion: str | None = Field(
        default=None, max_length=200, description='e.g. "weekly 300.00 x 52 / 12 = 1300.00"'
    )
    dismissed: Dismissal | None = None


class MissingDocument(_Model):
    """Named apart from 003's `MissingItem`, which the OpenAPI document already uses."""

    member_id: UUID
    job_id: UUID
    kind: MissingDocumentKind


class VerificationRequest(_Model):
    case_id: UUID
    household_id: UUID
    # Received date for an application, start date for a redetermination (FR-002-15). Optional
    # only so histories recorded before this field existed still decode.
    as_of_date: date | None = None


class VerificationResult(_Model):
    """Output of `verify_documents` (FR-002-23).

    `complete` and `needs_review` keep the names of 001's `VerificationOutcome`, and every
    added field has a default, so recorded workflow histories still replay (TW-14).
    `identity_verified` is None when the check did not look at identity (the 001 stub).
    """

    complete: bool
    needs_review: bool
    case_id: UUID | None = None
    identity_verified: bool | None = None
    findings: list[Finding] = Field(default_factory=list)
    missing_items: list[MissingDocument] = Field(default_factory=list)


class Correction(_Model):
    """A caseworker's correction of one entered detail (FR-002-19)."""

    field: str = Field(pattern=r"^(employee_name|employer|frequency|gross_pay|pay_date)$")
    old_value: str
    new_value: str
    reviewer_id: UUID
    reason: str = Field(min_length=1, max_length=REASON_MAX_LENGTH)
    corrected_at: datetime


class PayStubDetails(_Model):
    """What the applicant typed for one uploaded pay stub (FR-002-21). `member_id` is the
    household member the employee name matched, or None (FR-002-14)."""

    document_id: UUID
    case_id: UUID
    household_id: UUID
    member_id: UUID | None = None
    employee_name: str = Field(min_length=1, max_length=TEXT_MAX_LENGTH)
    employer: str = Field(min_length=1, max_length=TEXT_MAX_LENGTH)
    frequency: PayFrequency
    gross_pay: Decimal = Field(gt=0, decimal_places=2)
    pay_date: date
    corrections: list[Correction] = Field(default_factory=list)


class ReferenceJob(_Model):
    employer: str = Field(min_length=1, max_length=TEXT_MAX_LENGTH)
    monthly_gross: Decimal = Field(ge=0, decimal_places=2)


class ReferenceLookup(_Model):
    """Input of the reference lookup (FR-002-22). Matching is exact on case-folded names and
    the date of birth."""

    household_id: UUID
    member_id: UUID
    given_name: str = Field(min_length=1, max_length=TEXT_MAX_LENGTH)
    family_name: str = Field(min_length=1, max_length=TEXT_MAX_LENGTH)
    date_of_birth: date


class ReferenceAnswer(_Model):
    member_id: UUID
    identity: IdentityMatch
    jobs: list[ReferenceJob] = Field(default_factory=list)
