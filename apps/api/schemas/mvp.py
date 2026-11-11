"""Request and response bodies of the MVP contract endpoints (specs/mvp-contracts.md section 3).

Stubbed in the contracts PR; each feature fills in its own endpoints and may move its models to
a schema module of its own, keeping the names (the frontend's generated client uses them).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from packages.domain.case import (
    CaseOutcome,
    CaseSummary,
    CaseType,
    CoverageStatus,
    NoticeType,
    PayFrequency,
    Relationship,
)
from packages.domain.explanation import DecisionExplanation
from packages.domain.notice import RequestedItem
from packages.domain.verification import PayStubDetails, VerificationResult

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # FR-002-17
MAX_UPLOADS = 10

UploadType = Literal["application/pdf", "image/png", "image/jpeg"]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Response(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


# --- 002: pay stubs and the check -----------------------------------------------------------------


class PayStubUpload(_Body):
    """Step 1 of an upload: the details the applicant typed (FR-002-21). Step 2 sends the file's
    bytes to `PUT /cases/{case_id}/documents/{document_id}/content`."""

    employee_name: str = Field(min_length=1, max_length=100)
    employer: str = Field(min_length=1, max_length=100)
    frequency: PayFrequency
    gross_pay: Decimal = Field(gt=0, decimal_places=2)
    pay_date: date
    content_type: UploadType
    size_bytes: int = Field(gt=0, le=MAX_UPLOAD_BYTES)


class DocumentCreated(_Response):
    document_id: UUID


class DocumentView(_Response):
    document_id: UUID
    content_type: UploadType
    uploaded_at: datetime
    details: PayStubDetails


class CaseVerification(_Response):
    """The case page's documents section (FR-002-18)."""

    documents: list[DocumentView]
    result: VerificationResult | None = Field(description="null until the check has run")


# --- 004a: explanations ---------------------------------------------------------------------------


class ExplanationList(_Response):
    items: list[DecisionExplanation]


class HistoryEntry(_Response):
    case_id: UUID
    case_type: CaseType
    outcome: CaseOutcome | None
    decided_on: date | None
    coverage_start: date | None
    coverage_end: date | None
    explanation: DecisionExplanation | None = Field(description="Applicant explanation")


class ApplicantHistory(_Response):
    """FR-004-16: every coverage period's decision with the explanation given at the time."""

    items: list[HistoryEntry]


# --- 011: notices, requests and the demo clock ----------------------------------------------------


class NoticeSummary(_Response):
    notice_id: UUID
    notice_type: NoticeType
    delivered_at: datetime


class NoticeList(_Response):
    items: list[NoticeSummary]


class NoticeView(_Response):
    notice_id: UUID
    case_id: UUID
    notice_type: NoticeType
    text: str
    items: list[RequestedItem]
    due_case_date: date | None
    delivered_at: datetime


class OpenRequest(_Response):
    """An approved request for information the applicant has not answered (FR-011-10)."""

    notice_id: UUID
    case_id: UUID
    items: list[RequestedItem]
    due_case_date: date


class OpenRequestList(_Response):
    items: list[OpenRequest]


class RequestResponseBody(_Body):
    """The applicant's answer: documents uploaded first (002's endpoints), then confirmed items."""

    document_ids: list[UUID] = Field(default_factory=list, max_length=MAX_UPLOADS)
    confirmed_item_ids: list[UUID] = Field(default_factory=list, max_length=50)


# --- 007a: household page and on-demand redeterminations ------------------------------------------


class HouseholdMemberView(_Response):
    member_id: UUID
    full_name: str
    date_of_birth: date
    relationship: Relationship


class CoveragePeriodView(_Response):
    coverage_period_id: UUID
    start_date: date
    end_date: date
    status: CoverageStatus
    running_redetermination_case_id: UUID | None


class HouseholdView(_Response):
    """FR-007-18: members, coverage periods and cases."""

    household_id: UUID
    members: list[HouseholdMemberView]
    coverage_periods: list[CoveragePeriodView]
    cases: list[CaseSummary]


class RedeterminationStart(_Body):
    coverage_period_id: UUID


class RedeterminationStarted(_Response):
    case_id: UUID
    created: bool = Field(description="false when one was already running for the period")


# --- 008: program metrics -------------------------------------------------------------------------


class MetricName(StrEnum):
    average_days_to_decision = "average_days_to_decision"
    share_decided_within_deadline = "share_decided_within_deadline"
    automatic_renewal_rate = "automatic_renewal_rate"
    procedural_denial_rate = "procedural_denial_rate"


class MetricStatus(StrEnum):
    ok = "ok"
    no_data = "no_data"
    not_applicable = "not_applicable"


class MetricValue(_Response):
    name: MetricName
    value: Decimal | None = Field(description="null unless status is ok (FR-008-06)")
    status: MetricStatus


class MetricsReport(_Response):
    """FR-008-09: metric values only, no names or identifiers."""

    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    metrics: list[MetricValue]
