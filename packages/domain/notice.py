"""Notice contract (011, MVP): the notices sent to applicants, all from fixed templates.

FR-011-06 lists the types; `NOTICE_NEEDS_APPROVAL` says which wait for a caseworker (FR-011-08).
The API and the worker read the same table. Pure models only (BA-6, BA-8).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from packages.domain.case import NoticeType

NOTICE_NEEDS_APPROVAL: Mapping[NoticeType, bool] = MappingProxyType(
    {
        NoticeType.approval: False,
        NoticeType.automatic_renewal: False,
        NoticeType.renewal_after_response: False,
        NoticeType.denial: True,
        NoticeType.coverage_ended: True,
        NoticeType.request_for_information: True,
    }
)


class NoticeStatus(StrEnum):
    pending_approval = "pending_approval"
    delivered = "delivered"


class RequestedItemKind(StrEnum):
    pay_stub = "pay_stub"
    confirm_member = "confirm_member"
    confirm_job = "confirm_job"
    confirm_income = "confirm_income"


class RequestedItem(BaseModel):
    """One thing a request for information asks for (FR-011-12)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: UUID
    kind: RequestedItemKind
    member_id: UUID
    job_id: UUID | None = None
    label: str = Field(min_length=1, max_length=200, description="Text shown to the applicant")


class Notice(BaseModel):
    """A notice as stored in `notices`. Applicants see only delivered ones (FR-011-08)."""

    model_config = ConfigDict(extra="forbid", validate_by_name=True, validate_by_alias=True)

    id: UUID = Field(alias="_id", description="= uuid5(case_id, notice_type)")
    case_id: UUID
    household_id: UUID
    notice_type: NoticeType
    status: NoticeStatus
    text: str = Field(min_length=1, max_length=10_000)
    items: list[RequestedItem] = Field(
        default_factory=list, description="Requests for information only"
    )
    due_case_date: date | None = None
    created_at: datetime
    delivered_at: datetime | None = None
    synthetic: Literal[True] | None = None
