"""Case lifecycle contract (001): enums and models shared by the API, the worker and storage.

See specs/001-case-lifecycle/data-model.md and contracts/. Changing anything here needs
agreement from every lane that uses it (BA-8).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Review tasks and caseworker actions are 010's shapes (packages/domain/review.py, migration
# 0002_review_queue); 001 uses them as they are and re-exports them here (ADR-001-11).
from packages.domain.review import ActionOutcome as ActionOutcome
from packages.domain.review import ActionType as ActionType
from packages.domain.review import CaseworkerAction as CaseworkerAction
from packages.domain.review import CaseworkerActionSignal as CaseworkerActionSignal
from packages.domain.review import RejectionCode as RejectionCode
from packages.domain.review import ReviewReason as ReviewReason
from packages.domain.review import ReviewTask as ReviewTask
from packages.domain.review import ReviewTaskStatus as ReviewTaskStatus

# Namespace for deterministic case IDs (ADR-001-02). Never change it: stored case IDs depend on it.
SUBMISSION_NS = UUID("6f1c2a54-0b7e-5d3a-9c41-2e8f0d5b7a13")

AT_RISK_DAYS = 10
REASON_MAX_LENGTH = 500


def case_id_for(submission_reference: str | UUID) -> UUID:
    """One submission reference always maps to the same case ID (FR-001-01)."""
    return uuid5(SUBMISSION_NS, str(submission_reference))


def workflow_id_for(case_id: UUID) -> str:
    return f"case-{case_id}"


# --- Enums -----------------------------------------------------------------------------------


class CaseType(StrEnum):
    application = "application"
    redetermination = "redetermination"


class CaseState(StrEnum):
    submitted = "submitted"
    verifying = "verifying"
    awaiting_applicant = "awaiting_applicant"
    determining = "determining"
    under_review = "under_review"
    decided = "decided"


class CaseOutcome(StrEnum):
    approved = "approved"
    denied = "denied"
    # Reserved for 007.
    renewed_automatically = "renewed_automatically"
    renewed_after_response = "renewed_after_response"
    ended_by_caseworker = "ended_by_caseworker"


class DenialReason(StrEnum):
    income_over_limit = "income_over_limit"
    other_rule_not_met = "other_rule_not_met"
    missing_information = "missing_information"


class CaseEventKind(StrEnum):
    state_changed = "state_changed"
    review_task_opened = "review_task_opened"
    caseworker_action = "caseworker_action"
    action_rejected = "action_rejected"
    notice_requested = "notice_requested"
    coverage_started = "coverage_started"
    demo_action = "demo_action"


class DeterminationOutcome(StrEnum):
    eligible = "eligible"
    ineligible = "ineligible"
    cannot_determine = "cannot_determine"


class SubmissionRefusal(StrEnum):
    household_has_open_case = "household_has_open_case"
    household_has_coverage = "household_has_coverage"
    # The reference already opened a case of another household; that case is never revealed.
    submission_reference_in_use = "submission_reference_in_use"


class CoverageStatus(StrEnum):
    active = "active"
    continued_pending_review = "continued_pending_review"
    renewed = "renewed"
    ended = "ended"


class EventActorType(StrEnum):
    system = "system"
    user = "user"


class NoticeType(StrEnum):
    # 001 values; the full enum is FR-011-06.
    approval = "approval"
    denial = "denial"


# The actions CaseWorkflow carries out itself until it composes 010's review loop (010 T018): the
# spec's interim set ("until 010 lands, review tasks offer only Approve denial and Override to
# approve"). 010's own actions for these reasons need that loop (ADR-001-11). A data table, so the
# API and the worker offer the same actions.
CASE_WORKFLOW_ACTIONS: dict[ReviewReason, tuple[ActionType, ...]] = {
    ReviewReason.adverse_determination: (
        ActionType.approve_denial,
        ActionType.override_to_approve,
    ),
    ReviewReason.cannot_determine: (
        ActionType.approve_denial,
        ActionType.override_to_approve,
    ),
}


def allowed_actions_for(reason: ReviewReason) -> list[ActionType]:
    """Caseworker actions 001 accepts on a review task; other reasons belong to 010."""
    return list(CASE_WORKFLOW_ACTIONS.get(reason, ()))


# --- Models ----------------------------------------------------------------------------------


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Document(BaseModel):
    """A stored document: `_id` in MongoDB, `id` in Python."""

    model_config = ConfigDict(extra="forbid", validate_by_name=True, validate_by_alias=True)


class CaseClock(_Model):
    """Case clock (ADR-001-03): case time = case_start + (real - real_start) * scale + offset."""

    real_start: datetime
    case_start: datetime
    scale: int = Field(ge=1, description="1, or 1440 in the demo profile")
    offset_seconds: int = 0


class CaseStart(_Model):
    """Input of CaseWorkflow."""

    case_id: UUID
    household_id: UUID
    case_type: CaseType
    submission_reference: str = Field(max_length=128)
    received_at: datetime
    clock: CaseClock


class CaseOverride(_Model):
    reviewer_id: UUID
    reason: str = Field(min_length=1, max_length=REASON_MAX_LENGTH)
    determination_id: UUID | None


class Case(_Document):
    id: UUID = Field(alias="_id")
    household_id: UUID
    case_id: UUID
    case_type: CaseType
    submission_reference: str = Field(max_length=128)
    state: CaseState
    open: bool
    received_on: date
    decision_deadline: date
    clock: CaseClock
    outcome: CaseOutcome | None = None
    denial_reason: DenialReason | None = None
    override: CaseOverride | None = None
    decided_on: date | None = None
    last_seq: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime


class CaseEvent(_Document):
    id: UUID = Field(alias="_id")
    case_id: UUID
    household_id: UUID
    seq: int = Field(ge=1)
    kind: CaseEventKind
    from_state: CaseState | None = None
    to_state: CaseState | None = None
    case_time: datetime
    real_time: datetime
    actor_type: EventActorType
    actor_id: UUID | None = None
    # Enum-backed code, never free text.
    reason_code: str = Field(max_length=64, pattern=r"^[a-z0-9_]+$")
    review_task_id: UUID | None = None
    created_at: datetime


class CoveragePeriod(_Document):
    id: UUID = Field(alias="_id", description='= uuid5(case_id, "coverage")')
    household_id: UUID
    case_id: UUID
    start_date: date
    end_date: date
    status: CoverageStatus


class NoticeRequest(_Document):
    id: UUID = Field(alias="_id", description="= uuid5(case_id, notice_type)")
    case_id: UUID
    household_id: UUID
    notice_type: NoticeType
    status: Literal["requested"] = "requested"
    requested_case_time: datetime


class Determination(_Model):
    """Minimal determination; 003 owns the full shape. 001 never changes it (FR-001-20)."""

    determination_id: UUID
    outcome: DeterminationOutcome
    reason_code: DenialReason | None = None
    failed_rules: list[DenialReason] = Field(default_factory=list)
    missing_items: list[str] = Field(default_factory=list)
    as_of_date: date


class VerificationRequest(_Model):
    case_id: UUID
    household_id: UUID


class VerificationOutcome(_Model):
    complete: bool
    needs_review: bool


class DeterminationRequest(_Model):
    case_id: UUID
    household_id: UUID
    as_of_date: date


class CaseUpdate(_Model):
    """Fields of `cases` a change sets; None = unchanged."""

    state: CaseState
    open: bool = True
    outcome: CaseOutcome | None = None
    denial_reason: DenialReason | None = None
    override: CaseOverride | None = None
    decided_on: date | None = None


class CaseChange(_Model):
    """Input of `record_case_change`: everything one step writes, in one transaction (KD-10)."""

    case_id: UUID
    household_id: UUID
    seq: int = Field(ge=1)
    event: CaseEvent
    case_update: CaseUpdate | None = None
    review_task: ReviewTask | None = None
    action: CaseworkerAction | None = None
    coverage: CoveragePeriod | None = None
    notice: NoticeRequest | None = None

    @model_validator(mode="after")
    def _parts_belong_to_case(self) -> Self:
        if self.event.seq != self.seq:
            raise ValueError("event.seq must equal seq")
        parts = [self.event, self.review_task, self.action, self.coverage, self.notice]
        for part in parts:
            if part is not None and (
                part.case_id != self.case_id or part.household_id != self.household_id
            ):
                raise ValueError("every part must belong to the same case and household")
        return self


class OpenReviewTask(_Model):
    task_id: UUID
    reason: ReviewReason
    allowed_actions: list[ActionType]


class CaseStatus(_Model):
    """Query result `status` and API response. `at_risk` is derived from `days_remaining`."""

    # Every field is always present in a response, so the response schema lists them as required.
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)

    case_id: UUID
    state: CaseState
    received_on: date
    decision_deadline: date
    days_remaining: int | None = Field(default=None, description="null once decided")
    at_risk: bool = False
    outcome: CaseOutcome | None = None
    denial_reason: DenialReason | None = None
    open_review_task: OpenReviewTask | None = None

    @model_validator(mode="after")
    def _derive_at_risk(self) -> Self:
        self.at_risk = self.days_remaining is not None and self.days_remaining <= AT_RISK_DAYS
        return self


class AllowedActions(_Model):
    """Query result `allowed_actions`; empty when no task is open."""

    task_id: UUID | None = None
    actions: list[ActionType] = Field(default_factory=list)


# --- Application form (ADR-001-08, FR-001-24) --------------------------------------------------


class Relationship(StrEnum):
    self = "self"
    spouse = "spouse"
    partner = "partner"
    child = "child"
    parent = "parent"
    sibling = "sibling"
    other_relative = "other_relative"
    other = "other"


class CitizenshipStatus(StrEnum):
    us_citizen = "us_citizen"
    qualifying_immigrant = "qualifying_immigrant"
    neither = "neither"


class PayFrequency(StrEnum):
    weekly = "weekly"
    biweekly = "biweekly"
    semimonthly = "semimonthly"
    monthly = "monthly"


class Job(_Model):
    employer: str = Field(min_length=1, max_length=100)
    pay_frequency: PayFrequency
    declared_monthly_gross: Decimal = Field(ge=0, decimal_places=2)


class ApplicantMember(_Model):
    full_name: str = Field(min_length=1, max_length=100)
    date_of_birth: date
    relationship: Relationship
    citizenship_status: CitizenshipStatus
    medicare_entitled: bool
    zero_income: bool
    jobs: list[Job] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if self.date_of_birth >= date.today():
            raise ValueError("date_of_birth must be in the past")
        if self.zero_income == bool(self.jobs):
            raise ValueError("exactly one of zero_income or jobs is required")
        return self


class IllinoisAddress(_Model):
    line1: str = Field(min_length=1, max_length=100)
    line2: str | None = Field(default=None, max_length=100)
    city: str = Field(min_length=1, max_length=60)
    state: Literal["IL"]
    postal_code: str = Field(pattern=r"^\d{5}$")


class ApplicationForm(_Model):
    members: list[ApplicantMember] = Field(min_length=1, max_length=10)
    address: IllinoisAddress

    @model_validator(mode="after")
    def _one_self(self) -> Self:
        if sum(m.relationship is Relationship.self for m in self.members) != 1:
            raise ValueError("exactly one member must have relationship self")
        return self


class ApplicationSubmission(_Model):
    """API body; the household comes from the session, never from the body."""

    submission_reference: UUID
    form: ApplicationForm


class StoredApplication(_Document):
    id: UUID = Field(alias="_id")
    case_id: UUID
    household_id: UUID
    submission_reference: str = Field(max_length=128)
    form: ApplicationForm
    submitted_at: datetime
    synthetic: Literal[True]

    @model_validator(mode="after")
    def _id_is_case_id(self) -> Self:
        if self.id != self.case_id:
            raise ValueError("_id must equal case_id")
        return self


# --- Read models (ADR-001-07; API only, not stored) --------------------------------------------


class CaseSummary(_Model):
    case_id: UUID
    case_type: CaseType
    state: CaseState
    received_on: date
    decision_deadline: date
    days_remaining: int | None = None
    at_risk: bool = False
    outcome: CaseOutcome | None = None


class CasePage(_Model):
    items: list[CaseSummary]
    next_cursor: str | None


class CoverageSummary(_Model):
    start_date: date
    end_date: date
    status: CoverageStatus


class OverrideSummary(_Model):
    reviewer_name: str
    reason: str | None = Field(default=None, description="null for applicants")
    decided_on: date


class CaseDetail(CaseStatus):
    case_type: CaseType
    coverage_period: CoverageSummary | None = None
    override: OverrideSummary | None = None
    demo_clock: bool = False


class CaseHistoryEntry(_Model):
    seq: int
    kind: CaseEventKind
    from_state: CaseState | None = None
    to_state: CaseState | None = None
    case_time: datetime
    real_time: datetime
    actor: str = Field(description="System or a display name")
    actor_type: EventActorType
    reason_code: str


class CaseHistoryPage(_Model):
    items: list[CaseHistoryEntry]
    next_cursor: int | None
