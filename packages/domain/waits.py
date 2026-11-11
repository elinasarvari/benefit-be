"""Wait contract (011, MVP): timer names, the pending-timer view, and the applicant-response and
advance-clock signal payloads (TW-5). See specs/mvp-contracts.md section 2 in the metarepo.

Pure models only (BA-6, BA-8).
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class TimerName(StrEnum):
    response_deadline = "response_deadline"  # 15 days after a request for information (FR-011-01)
    review_reminder = "review_reminder"  # FR-010-02
    review_escalation = "review_escalation"  # FR-010-02
    coverage_end = "coverage_end"  # escalates an open redetermination review (FR-007-14)


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PendingTimer(_Contract):
    """The case's next timer, on the case clock. "Advance clock" fires it (FR-011-05)."""

    name: TimerName
    due_case_time: AwareDatetime


class ApplicantResponseSignal(_Contract):
    """Payload of the `applicant_response` signal (FR-011-03).

    The API stores the documents, pay stub details and any new snapshot first; the signal only
    refers to them.
    """

    response_id: UUID
    notice_id: UUID = Field(description="The request for information being answered")
    snapshot_id: UUID | None = None
    document_ids: list[UUID] = Field(default_factory=list, max_length=10)
    submitted_at_real: AwareDatetime


class AdvanceClockSignal(_Contract):
    """Payload of the `advance_clock` signal (FR-011-05). The workflow ignores it unless the
    case clock is in the demo profile; the API refuses it outside the demo profile too."""

    action_id: UUID
    requested_by: UUID
    requested_at_real: AwareDatetime
