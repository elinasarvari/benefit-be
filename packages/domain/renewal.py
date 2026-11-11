"""Redetermination contract (007): the renewal check, the held request and the daily schedule.

Codes, IDs and counts only; labels are built where the request is stored (TW-4). Pure models only
(BA-6, BA-8). See specs/007-redeterminations/data-model.md.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.domain.case import CaseStart, DeterminationOutcome

REDETERMINATION_LEAD_DAYS = 60  # the schedule opens periods ending within this many days


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RenewalItemKind(StrEnum):
    identity_mismatch = "identity_mismatch"
    income_differs = "income_differs"
    undeclared_job = "undeclared_job"
    job_missing_in_lookup = "job_missing_in_lookup"


class RenewalItem(_Contract):
    kind: RenewalItemKind
    member_id: UUID
    job_id: UUID | None = None
    reference_job_index: int | None = Field(default=None, ge=0, description="undeclared_job only")


class RenewalCheckRequest(_Contract):
    case_id: UUID
    household_id: UUID
    coverage_period_id: UUID
    as_of_date: date


class RenewalCheck(_Contract):
    renewal_check_id: UUID = Field(description='= uuid5(case_id, "renewal_check")')
    case_id: UUID
    household_id: UUID
    coverage_period_id: UUID
    coverage_end: date = Field(description="End of the period being renewed")
    items: list[RenewalItem] = Field(default_factory=list)
    complete: bool

    @model_validator(mode="after")
    def _complete_means_no_items(self) -> Self:
        if self.complete != (not self.items):
            raise ValueError("complete must equal 'no items'")
        return self


class RenewalRequestDraft(_Contract):
    case_id: UUID
    household_id: UUID
    renewal_check_id: UUID
    determination_outcome: DeterminationOutcome
    items: list[RenewalItem]


class RenewalRequestHeld(_Contract):
    notice_id: UUID = Field(description='= uuid5(case_id, "request_for_information")')
    item_count: int = Field(ge=0)


class RequestDelivery(_Contract):
    case_id: UUID
    notice_id: UUID
    due_case_date: date


class DueRedeterminationsRequest(_Contract):
    as_of_date: date


class DueRedeterminations(_Contract):
    starts: list[CaseStart] = Field(default_factory=list, max_length=200)
