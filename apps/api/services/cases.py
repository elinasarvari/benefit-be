"""Case use cases of the API (001): open a case, read cases, history and detail, send an action.

The API only opens the case (ADR-001-02) and talks to `CaseWorkflow` through Temporal; every later
change is the workflow's (ADR-001-01, PL-2). No log line carries `reason` text or household
data (PL-11, constitution VIII).
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol, TypeVar
from uuid import UUID

import structlog

from apps.api.repositories.cases import CaseAlreadyExists, HouseholdHasOpenCase
from apps.api.schemas.cases import ReviewActionBody
from packages.domain.audit import (
    ActorType,
    AuditAction,
    AuditLogEntry,
    AuditOutcome,
    ReasonCode,
)
from packages.domain.auth import Principal, Role
from packages.domain.case import (
    ActionOutcome,
    AllowedActions,
    ApplicationSubmission,
    Case,
    CaseClock,
    CaseDetail,
    CaseEvent,
    CaseHistoryEntry,
    CaseHistoryPage,
    CasePage,
    CaseStart,
    CaseState,
    CaseStatus,
    CaseSummary,
    CaseType,
    CaseworkerAction,
    CaseworkerActionRequest,
    CoveragePeriod,
    CoverageSummary,
    EventActorType,
    OpenReviewTask,
    OverrideSummary,
    StoredApplication,
    SubmissionRefusal,
    case_id_for,
    workflow_id_for,
)
from packages.domain.errors import FieldError
from packages.platform.audit import AuditWriter
from packages.platform.clock import Clock, case_now, case_today
from packages.platform.errors import Conflict, Forbidden, InvalidInput, NotFound
from packages.platform.logging import bind_context
from packages.platform.scope import Scope

T = TypeVar("T")

DECISION_DAYS = 45  # calendar days from receipt (FR-001-11)
DEMO_SCALE = 1440  # one real minute = one case day (ADR-001-03)
DEFAULT_LIST_LIMIT = 50
DEFAULT_HISTORY_LIMIT = 100
SYSTEM_ACTOR = "System"
UNKNOWN_ACTOR = "Unknown user"

REFUSAL_MESSAGE = {
    SubmissionRefusal.household_has_open_case: "This household already has an open case.",
    SubmissionRefusal.household_has_coverage: "This household already has coverage.",
    SubmissionRefusal.submission_reference_in_use: (
        "This submission reference is already in use. Start a new application."
    ),
}
REFUSAL_FIELD = {SubmissionRefusal.submission_reference_in_use: "submission_reference"}

log = structlog.get_logger()


class CaseTransaction(Protocol):
    async def find_case(self, scope: Scope, case_id: UUID) -> Case | None: ...

    async def active_coverage(
        self, scope: Scope, household_id: UUID, day: date
    ) -> CoveragePeriod | None: ...

    async def insert_case(self, scope: Scope, case: Case) -> None: ...

    async def insert_application(self, scope: Scope, application: StoredApplication) -> None: ...


class CaseRepository(Protocol):
    async def run_in_transaction(self, work: Callable[[CaseTransaction], Awaitable[T]]) -> T: ...

    async def find_case(self, scope: Scope, case_id: UUID) -> Case | None: ...

    async def open_review_task(self, scope: Scope, case_id: UUID) -> OpenReviewTask | None: ...

    async def find_open_case_id(self, scope: Scope, household_id: UUID) -> UUID | None: ...

    async def coverage_for_case(self, scope: Scope, case_id: UUID) -> CoveragePeriod | None: ...

    async def list_cases(
        self,
        scope: Scope,
        *,
        state: CaseState | None,
        after: tuple[date, UUID] | None,
        limit: int,
    ) -> list[Case]: ...

    async def list_events(
        self, scope: Scope, case_id: UUID, *, after_seq: int, limit: int
    ) -> list[CaseEvent]: ...

    async def record_rejected_action(
        self, scope: Scope, action: CaseworkerAction, entry: AuditLogEntry, audit: AuditWriter
    ) -> None: ...


class UserNames(Protocol):
    async def display_names(self, ids: list[UUID]) -> dict[UUID, str]: ...


class CaseWorkflows(Protocol):
    async def start(self, start: CaseStart) -> None: ...

    async def allowed_actions(self, case_id: UUID) -> AllowedActions: ...

    async def signal_action(
        self, case_id: UUID, request: CaseworkerActionRequest, *, best_effort: bool = False
    ) -> bool:
        """True when delivered. With `best_effort`, a failed delivery returns False."""
        ...


@dataclass(frozen=True)
class SubmissionResult:
    status: CaseStatus
    created: bool


def _refused(reason: SubmissionRefusal, related_case_id: UUID | None = None) -> Conflict:
    """`related_case_id` is the caller's own household's case, never another household's (R12)."""
    return Conflict(
        REFUSAL_MESSAGE[reason],
        related_case_id=related_case_id,
        fields=[
            FieldError(
                field=REFUSAL_FIELD.get(reason, "household"),
                message=REFUSAL_MESSAGE[reason],
                type=reason.value,
            )
        ],
    )


def status_of(case: Case, task: OpenReviewTask | None, real_now: datetime) -> CaseStatus:
    """Days remaining from the case clock (same rule as the workflow `status` query, R3)."""
    decided = case.state is CaseState.decided
    days = None if decided else (case.decision_deadline - case_today(case.clock, real_now)).days
    return CaseStatus(
        case_id=case.case_id,
        state=case.state,
        received_on=case.received_on,
        decision_deadline=case.decision_deadline,
        days_remaining=days,
        outcome=case.outcome,
        denial_reason=case.denial_reason,
        open_review_task=None if decided else task,
    )


def _start_of(case: Case) -> CaseStart:
    """Built from the stored case, so a retried submission starts the identical workflow."""
    return CaseStart(
        case_id=case.case_id,
        household_id=case.household_id,
        case_type=case.case_type,
        submission_reference=case.submission_reference,
        received_at=case.clock.real_start,
        clock=case.clock,
    )


def _new_case(case_id: UUID, household_id: UUID, reference: str, now: datetime, scale: int) -> Case:
    clock = CaseClock(real_start=now, case_start=now, scale=scale)
    received_on = case_today(clock, now)
    return Case(
        id=case_id,
        case_id=case_id,
        household_id=household_id,
        case_type=CaseType.application,
        submission_reference=reference,
        state=CaseState.submitted,
        open=True,
        received_on=received_on,
        decision_deadline=received_on + timedelta(days=DECISION_DAYS),
        clock=clock,
        created_at=now,
        updated_at=now,
    )


async def submit_application(
    body: ApplicationSubmission,
    principal: Principal,
    *,
    repo: CaseRepository,
    workflows: CaseWorkflows,
    clock: Clock,
    demo_profile: bool,
) -> SubmissionResult:
    """Open one case per submission reference, then start its workflow (ADR-001-02)."""
    if principal.role is not Role.applicant or principal.household_id is None:
        raise Forbidden()
    household_id = principal.household_id  # never from the body (PL-6)
    case_id = case_id_for(body.submission_reference)
    bind_context(case_id=str(case_id), workflow_id=workflow_id_for(case_id))
    scope = Scope.for_principal(principal)
    now = clock.now()
    candidate = _new_case(
        case_id,
        household_id,
        str(body.submission_reference),
        now,
        DEMO_SCALE if demo_profile else 1,
    )
    application = StoredApplication(
        id=case_id,
        case_id=case_id,
        household_id=household_id,
        submission_reference=str(body.submission_reference),
        form=body.form,
        submitted_at=case_now(candidate.clock, now),
        synthetic=True,
    )

    async def open_case(tx: CaseTransaction) -> tuple[Case, bool]:
        existing = await tx.find_case(scope, case_id)
        if existing is not None:
            return existing, False
        period = await tx.active_coverage(scope, household_id, candidate.received_on)
        if period is not None:
            raise _refused(SubmissionRefusal.household_has_coverage, period.case_id)
        await tx.insert_case(scope, candidate)
        await tx.insert_application(scope, application)  # same transaction as the case
        return candidate, True

    try:
        try:
            case, created = await repo.run_in_transaction(open_case)
        except HouseholdHasOpenCase:
            # The aborted transaction can't read; the open case is looked up afterwards.
            open_id = await repo.find_open_case_id(scope, household_id)
            raise _refused(SubmissionRefusal.household_has_open_case, open_id) from None
        except CaseAlreadyExists:
            # A concurrent request with the same reference won; return its case. If this caller
            # can't see it, the reference belongs to another household's case.
            found = await repo.find_case(scope, case_id)
            if found is None:
                raise _refused(SubmissionRefusal.submission_reference_in_use) from None
            case, created = found, False
    except Conflict as refused:
        log.info("case.submission_refused", reason_code=refused.fields[0].type)
        raise

    # Started on every call: a retry after Temporal was down starts it; already started = success.
    await workflows.start(_start_of(case))
    log.info("case.submitted", created=created)
    return SubmissionResult(status_of(case, None, now), created)


def _encode_cursor(case: Case) -> str:
    raw = f"{case.decision_deadline.isoformat()}|{case.id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[date, UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        deadline, case_id = raw.split("|")
        return date.fromisoformat(deadline), UUID(case_id)
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise InvalidInput(
            fields=[FieldError(field="query.cursor", message="Invalid cursor.", type="invalid")]
        ) from None


def summary_of(case: Case, real_now: datetime) -> CaseSummary:
    status = status_of(case, None, real_now)
    return CaseSummary(
        case_id=case.case_id,
        case_type=case.case_type,
        state=case.state,
        received_on=case.received_on,
        decision_deadline=case.decision_deadline,
        days_remaining=status.days_remaining,
        at_risk=status.at_risk,
        outcome=case.outcome,
    )


async def list_cases(
    principal: Principal,
    *,
    repo: CaseRepository,
    clock: Clock,
    state: CaseState | None = None,
    cursor: str | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
) -> CasePage:
    """Soonest deadline first. An applicant gets their household's open cases (the scope adds the
    household; a `state` filter is ignored). Staff get open cases, or every case in `state`."""
    scope = Scope.for_principal(principal)
    after = _decode_cursor(cursor) if cursor else None
    if principal.role is Role.applicant:
        state = None
    rows = await repo.list_cases(scope, state=state, after=after, limit=limit + 1)
    page = rows[:limit]
    now = clock.now()
    return CasePage(
        items=[summary_of(case, now) for case in page],
        next_cursor=_encode_cursor(page[-1]) if len(rows) > limit else None,
    )


def _audit_wrong_case(
    case_id: UUID, principal: Principal, now: datetime, reference_id: str
) -> AuditLogEntry:
    return AuditLogEntry(
        occurred_at=now,
        actor_type=ActorType.user,
        actor_id=str(principal.user_id),
        actor_role=principal.role,
        action=AuditAction.access_refused,
        target_type="case",
        target_id=str(case_id),
        outcome=AuditOutcome.refused,
        reason_code=ReasonCode.wrong_case,
        reference_id=reference_id,
    )


async def get_case_detail(
    case_id: UUID,
    principal: Principal,
    *,
    repo: CaseRepository,
    users: UserNames,
    audit: AuditWriter,
    clock: Clock,
    reference_id: str,
) -> CaseDetail:
    """Applicants see only their own household's cases; anything else is the same 404 as a missing
    case (PL-6). An applicant's miss is audited without ever looking outside their scope (R12)."""
    bind_context(case_id=str(case_id), workflow_id=workflow_id_for(case_id))
    scope = Scope.for_principal(principal)
    now = clock.now()
    case = await repo.find_case(scope, case_id)
    if case is None:
        if principal.role is Role.applicant:
            await audit.write(_audit_wrong_case(case_id, principal, now, reference_id))
            log.info("case.access_refused", reason_code=ReasonCode.wrong_case.value)
        raise NotFound()
    task = await repo.open_review_task(scope, case_id)
    status = status_of(case, task, now)
    coverage = None
    override = None
    if case.state is CaseState.decided:
        period = await repo.coverage_for_case(scope, case_id)
        if period is not None:
            coverage = CoverageSummary(
                start_date=period.start_date, end_date=period.end_date, status=period.status
            )
    if case.override is not None:
        names = await users.display_names([case.override.reviewer_id])
        override = OverrideSummary(
            reviewer_name=names.get(case.override.reviewer_id, UNKNOWN_ACTOR),
            # The reviewer's reason is for staff only (FR-001-20, constitution VIII).
            reason=None if principal.role is Role.applicant else case.override.reason,
            decided_on=case.decided_on or case.received_on,
        )
    return CaseDetail(
        **status.model_dump(),
        case_type=case.case_type,
        coverage_period=coverage,
        override=override,
        demo_clock=case.clock.scale > 1,
    )


async def get_case_history(
    case_id: UUID,
    principal: Principal,
    *,
    repo: CaseRepository,
    users: UserNames,
    cursor: int = 0,
    limit: int = DEFAULT_HISTORY_LIMIT,
) -> CaseHistoryPage:
    """Events by `seq`; the actor is "System" or a display name (caseworkers, supervisors)."""
    bind_context(case_id=str(case_id), workflow_id=workflow_id_for(case_id))
    scope = Scope.for_principal(principal)
    if await repo.find_case(scope, case_id) is None:
        raise NotFound()
    rows = await repo.list_events(scope, case_id, after_seq=cursor, limit=limit + 1)
    page = rows[:limit]
    names = await users.display_names([e.actor_id for e in page if e.actor_id is not None])

    def actor(event: CaseEvent) -> str:
        if event.actor_type is EventActorType.system or event.actor_id is None:
            return SYSTEM_ACTOR
        return names.get(event.actor_id, UNKNOWN_ACTOR)

    return CaseHistoryPage(
        items=[
            CaseHistoryEntry(
                seq=e.seq,
                kind=e.kind,
                from_state=e.from_state,
                to_state=e.to_state,
                case_time=e.case_time,
                real_time=e.real_time,
                actor=actor(e),
                actor_type=e.actor_type,
                reason_code=e.reason_code,
            )
            for e in page
        ],
        next_cursor=page[-1].seq if len(rows) > limit else None,
    )


async def submit_review_action(
    case_id: UUID,
    task_id: UUID,
    body: ReviewActionBody,
    principal: Principal,
    *,
    workflows: CaseWorkflows,
    repo: CaseRepository,
    audit: AuditWriter,
    clock: Clock,
) -> None:
    """Pre-check `allowed_actions`, then signal (ADR-001-04 Option B).

    A refused action is signalled best effort, so a running workflow records it as
    `action_rejected` (US1-AS5). A decided case's workflow has completed and can't take the
    signal, so the API records the refusal through the repository instead. Either way the
    caller gets a 409.
    """
    bind_context(case_id=str(case_id), workflow_id=workflow_id_for(case_id))
    request = CaseworkerActionRequest(
        task_id=task_id,
        action=body.action,
        reviewer_id=principal.user_id,  # never from the body
        reason=body.reason,
        request_id=body.request_id,
    )
    allowed = await workflows.allowed_actions(case_id)
    if allowed.task_id != task_id or body.action not in allowed.actions:
        if not await workflows.signal_action(case_id, request, best_effort=True):
            await _record_refusal(case_id, request, principal, repo=repo, audit=audit, clock=clock)
        log.info("case.action_refused", action=body.action.value, task_id=str(task_id))
        raise Conflict("This action is not allowed for this task.")
    await workflows.signal_action(case_id, request)
    log.info("case.action_signalled", action=body.action.value, task_id=str(task_id))


async def _record_refusal(
    case_id: UUID,
    request: CaseworkerActionRequest,
    principal: Principal,
    *,
    repo: CaseRepository,
    audit: AuditWriter,
    clock: Clock,
) -> None:
    """`caseworker_actions` with `outcome: rejected` plus its audit entry, keyed by request_id."""
    scope = Scope.for_principal(principal)
    case = await repo.find_case(scope, case_id)
    if case is None:
        raise NotFound()
    now = clock.now()
    action = CaseworkerAction(
        id=request.request_id,
        case_id=case.case_id,
        household_id=case.household_id,
        task_id=request.task_id,
        action=request.action,
        reviewer_id=request.reviewer_id,
        reason=request.reason,
        outcome=ActionOutcome.rejected,
        case_time=case_now(case.clock, now),
        real_time=now,
    )
    entry = AuditLogEntry(
        occurred_at=now,
        actor_type=ActorType.user,
        actor_id=str(principal.user_id),
        actor_role=principal.role,
        action=AuditAction.caseworker_action,
        target_type="case",
        target_id=str(case_id),
        outcome=AuditOutcome.refused,
        reference_id=str(request.request_id),
    )
    await repo.record_rejected_action(scope, action, entry, audit)
