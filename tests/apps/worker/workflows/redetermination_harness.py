"""Stubs and helpers for redetermination workflow tests (TW-13, TW-15): no DB, no agent.

Builds on `case_harness`: the same time-skipping environment and the same `CaseWorkflow`, started
with a `redetermination` case. All data is synthetic.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from temporalio import activity
from temporalio.client import Client, WorkflowHandle
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from apps.worker.review_payloads import ReviewFlagUpdate
from apps.worker.workflows.case import CaseWorkflow
from packages.domain.case import (
    CaseClock,
    CaseStart,
    CaseState,
    CaseType,
    Determination,
    DeterminationRequest,
)
from packages.domain.renewal import (
    RenewalCheck,
    RenewalCheckRequest,
    RenewalItem,
    RenewalItemKind,
    RenewalRequestDraft,
    RenewalRequestHeld,
    RequestDelivery,
)
from packages.domain.review import ActionType
from packages.domain.verification import VerificationResult
from packages.domain.waits import ApplicantResponseSignal
from tests.apps.worker.workflows.case_harness import (
    CASE_START,
    HOUSEHOLD,
    TASK_QUEUE,
    Stubs,
    action,
    signal,
    wait_for_state,
)

PERIOD_ID = uuid.UUID("00000000-0000-4000-8000-0000000000e1")
MEMBER_ID = uuid.UUID("00000000-0000-4000-8000-0000000000e2")
JOB_ID = uuid.UUID("00000000-0000-4000-8000-0000000000e3")
PERIOD_END = date(2026, 4, 30)  # 44 days after CASE_START: the coverage end is reached by skipping
OUT_OF_DATE = [RenewalItem(kind=RenewalItemKind.income_differs, member_id=MEMBER_ID, job_id=JOB_ID)]


@dataclass
class RenewalStubs(Stubs):
    """`Stubs` plus the renewal activities and the review flag."""

    case_id: uuid.UUID = field(default_factory=uuid.uuid4)
    period_end: date = PERIOD_END
    items: list[RenewalItem] = field(default_factory=list)
    verification: VerificationResult = field(
        default_factory=lambda: VerificationResult(complete=True, needs_review=False)
    )
    check_failures: int = 0
    prepare_failures: int = 0
    check_attempts: int = 0
    prepare_attempts: int = 0
    checks: list[RenewalCheckRequest] = field(default_factory=list)
    drafts: list[RenewalRequestDraft] = field(default_factory=list)
    deliveries: list[RequestDelivery] = field(default_factory=list)
    flags: list[ReviewFlagUpdate] = field(default_factory=list)
    after_response: Determination | None = None  # the determination once the applicant answered

    def activities(self) -> list[Callable[..., Any]]:
        @activity.defn(name="check_renewal")
        async def check_renewal(request: RenewalCheckRequest) -> RenewalCheck:
            self.check_attempts += 1
            if self.check_failures > 0:
                self.check_failures -= 1
                raise RuntimeError("synthetic reference lookup outage")
            self.checks.append(request)
            return RenewalCheck(
                renewal_check_id=uuid.uuid5(request.case_id, "renewal_check"),
                case_id=request.case_id,
                household_id=request.household_id,
                coverage_period_id=request.coverage_period_id,
                coverage_end=self.period_end,
                items=self.items,
                complete=not self.items,
            )

        @activity.defn(name="prepare_renewal_request")
        async def prepare_renewal_request(draft: RenewalRequestDraft) -> RenewalRequestHeld:
            self.prepare_attempts += 1
            if self.prepare_failures > 0:
                self.prepare_failures -= 1
                raise RuntimeError("synthetic notice store outage")
            self.drafts.append(draft)
            return RenewalRequestHeld(
                notice_id=uuid.uuid5(draft.case_id, "request_for_information"),
                item_count=len(draft.items),
            )

        @activity.defn(name="deliver_renewal_request")
        async def deliver_renewal_request(delivery: RequestDelivery) -> None:
            self.deliveries.append(delivery)

        @activity.defn(name="set_review_flag")
        async def set_review_flag(update: ReviewFlagUpdate) -> bool:
            self.flags.append(update)
            return True

        @activity.defn(name="verify_documents")
        async def verify_documents(request: Any) -> VerificationResult:
            self.verify_calls += 1
            return self.verification

        @activity.defn(name="determine_eligibility")
        async def determine_eligibility(request: DeterminationRequest) -> Determination:
            result = self.result
            if self.determine_calls and self.after_response is not None:
                result = self.after_response
            self.determine_calls.append(request)
            return result.model_copy(update={"as_of_date": request.as_of_date})

        skip = {"verify_documents", "determine_eligibility"}
        base = [a for a in super().activities() if _name(a) not in skip]
        return [
            *base,
            verify_documents,
            determine_eligibility,
            check_renewal,
            prepare_renewal_request,
            deliver_renewal_request,
            set_review_flag,
        ]

    def states(self) -> list[str]:
        return [c.event.to_state.value for c in self.changes if c.event.to_state is not None]

    def notice_id(self) -> uuid.UUID:
        return uuid.uuid5(self.case_id, "request_for_information")


def _name(func: Callable[..., Any]) -> str:
    return str(getattr(func, "__temporal_activity_definition").name)  # noqa: B009


def redetermination_start(real_start: datetime, case_id: uuid.UUID, scale: int = 1) -> CaseStart:
    return CaseStart(
        case_id=case_id,
        household_id=HOUSEHOLD,
        case_type=CaseType.redetermination,
        submission_reference=str(PERIOD_ID),
        received_at=CASE_START,
        clock=CaseClock(real_start=real_start, case_start=CASE_START, scale=scale),
        coverage_period_id=PERIOD_ID,
    )


def worker(client: Client, stubs: Stubs, **options: Any) -> Worker:
    return Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[CaseWorkflow],
        activities=stubs.activities(),
        **options,
    )


async def start(
    env: WorkflowEnvironment, stubs: RenewalStubs, scale: int = 1
) -> tuple[WorkflowHandle[Any, Any], CaseStart]:
    data = redetermination_start(await env.get_current_time(), stubs.case_id, scale)
    handle = await env.client.start_workflow(
        CaseWorkflow.run, data, id=f"case-{data.case_id}", task_queue=TASK_QUEUE
    )
    return handle, data


def response(notice_id: uuid.UUID) -> ApplicantResponseSignal:
    return ApplicantResponseSignal(
        response_id=uuid.uuid4(), notice_id=notice_id, submitted_at_real=datetime.now(UTC)
    )


async def send_response(handle: WorkflowHandle[Any, Any], payload: ApplicantResponseSignal) -> None:
    await handle.signal("applicant_response", payload)


async def reach_awaiting_applicant(
    handle: WorkflowHandle[Any, Any], stubs: RenewalStubs
) -> uuid.UUID:
    """Approve the held request; returns the request's notice ID."""
    held = await wait_for_state(handle, CaseState.under_review)
    assert held.open_review_task is not None
    await signal(handle, action(held.open_review_task.task_id, ActionType.approve))
    await wait_for_state(handle, CaseState.awaiting_applicant)
    return stubs.notice_id()
