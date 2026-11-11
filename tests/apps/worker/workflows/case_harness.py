"""Stubbed activities and helpers for CaseWorkflow tests (TW-13, TW-15): no DB, no agent."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from temporalio import activity
from temporalio.client import Client, WorkflowHandle
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from apps.worker.workflows.case import CaseWorkflow
from packages.domain.auth import Role
from packages.domain.case import (
    ActionType,
    CaseChange,
    CaseClock,
    CaseStart,
    CaseState,
    CaseStatus,
    CaseType,
    CaseworkerActionSignal,
    DenialReason,
    Determination,
    DeterminationOutcome,
    DeterminationRequest,
)
from packages.domain.verification import VerificationRequest, VerificationResult

TASK_QUEUE = "test-case"
# Noon in Chicago on 17 March 2026, so a few skipped days never cross a date boundary by DST.
CASE_START = datetime(2026, 3, 17, 17, 0, tzinfo=UTC)
RECEIVED_ON = date(2026, 3, 17)
HOUSEHOLD = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
REVIEWER = uuid.UUID("00000000-0000-4000-8000-00000000c051")  # synthetic cw-synth-1
DETERMINATION_ID = uuid.UUID("00000000-0000-4000-8000-0000000000d1")
SYNTH_REASON = "SYNTH-REASON-123"


def determination(
    outcome: DeterminationOutcome,
    reason_code: DenialReason | None = None,
    missing_items: Sequence[str] = (),
    failed_rules: Sequence[DenialReason] = (),
) -> Determination:
    return Determination(
        determination_id=DETERMINATION_ID,
        outcome=outcome,
        reason_code=reason_code,
        failed_rules=list(failed_rules),
        missing_items=list(missing_items),
        as_of_date=RECEIVED_ON,
    )


ELIGIBLE = determination(DeterminationOutcome.eligible)
INELIGIBLE = determination(DeterminationOutcome.ineligible, DenialReason.income_over_limit)
CANNOT_DETERMINE = determination(DeterminationOutcome.cannot_determine, missing_items=["pay_stub"])


@dataclass
class Stubs:
    """Activity stubs that record what the workflow asked for."""

    result: Determination = field(default_factory=lambda: ELIGIBLE)
    changes: list[CaseChange] = field(default_factory=list)
    verify_calls: int = 0
    determine_calls: list[DeterminationRequest] = field(default_factory=list)
    on_change: Callable[[CaseChange], Any] | None = None
    on_verify: Callable[[int], Any] | None = None
    # Attempts that fail before the activity succeeds (a database outage, say).
    record_failures: int = 0
    determine_failures: int = 0
    record_attempts: int = 0
    determine_attempts: int = 0

    def activities(self) -> list[Callable[..., Any]]:
        @activity.defn(name="record_case_change")
        async def record_case_change(change: CaseChange) -> None:
            self.record_attempts += 1
            if self.record_failures > 0:
                self.record_failures -= 1
                raise RuntimeError("synthetic database outage")
            self.changes.append(change)
            if self.on_change is not None:
                await self.on_change(change)

        @activity.defn(name="verify_documents")
        async def verify_documents(request: VerificationRequest) -> VerificationResult:
            self.verify_calls += 1
            if self.on_verify is not None:
                await self.on_verify(self.verify_calls)
            return VerificationResult(complete=True, needs_review=False)

        @activity.defn(name="determine_eligibility")
        async def determine_eligibility(request: DeterminationRequest) -> Determination:
            self.determine_attempts += 1
            if self.determine_failures > 0:
                self.determine_failures -= 1
                raise RuntimeError("synthetic database outage")
            self.determine_calls.append(request)
            return self.result.model_copy(update={"as_of_date": request.as_of_date})

        return [record_case_change, verify_documents, determine_eligibility]

    def kinds_and_states(self) -> list[tuple[str, str | None, str | None]]:
        return [
            (c.event.kind.value, _v(c.event.from_state), _v(c.event.to_state)) for c in self.changes
        ]


def _v(state: CaseState | None) -> str | None:
    return state.value if state is not None else None


def case_start(real_start: datetime, scale: int = 1) -> CaseStart:
    case_id = uuid.uuid4()
    return CaseStart(
        case_id=case_id,
        household_id=HOUSEHOLD,
        case_type=CaseType.application,
        submission_reference=f"sub-{case_id}",
        received_at=CASE_START,
        clock=CaseClock(real_start=real_start, case_start=CASE_START, scale=scale),
    )


@asynccontextmanager
async def environment() -> AsyncIterator[WorkflowEnvironment]:
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        yield env


def worker(client: Client, stubs: Stubs, **options: Any) -> Worker:
    return Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[CaseWorkflow],
        activities=stubs.activities(),
        **options,
    )


async def start(
    env: WorkflowEnvironment, scale: int = 1
) -> tuple[WorkflowHandle[Any, Any], CaseStart]:
    data = case_start(await env.get_current_time(), scale)
    handle = await env.client.start_workflow(
        CaseWorkflow.run, data, id=f"case-{data.case_id}", task_queue=TASK_QUEUE
    )
    return handle, data


async def status_of(handle: WorkflowHandle[Any, Any]) -> CaseStatus:
    return await handle.query(CaseWorkflow.status)


async def wait_for_state(handle: WorkflowHandle[Any, Any], state: CaseState) -> CaseStatus:
    for _ in range(200):
        current = await status_of(handle)
        if current.state is state:
            return current
        await asyncio.sleep(0.05)
    raise AssertionError(f"the case never reached {state}")


async def wait_for_changes(stubs: Stubs, count: int) -> None:
    for _ in range(200):
        if len(stubs.changes) >= count:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"expected {count} changes, saw {len(stubs.changes)}")


def action(
    task_id: uuid.UUID,
    kind: ActionType,
    reason: str | None = None,
    request_id: uuid.UUID | None = None,
) -> CaseworkerActionSignal:
    """Build 010's signal payload (ADR-001-11). `model_construct` lets a test send what the API
    would refuse. `request_id` is the REST body's key, sent as the signal's `action_id`."""
    return CaseworkerActionSignal.model_construct(
        action_id=request_id or uuid.uuid4(),
        task_id=task_id,
        action=kind,
        reviewer_id=REVIEWER,
        reviewer_role=Role.caseworker,
        reason=reason,
        submitted_at=datetime.now(UTC),
    )


async def signal(handle: WorkflowHandle[Any, Any], request: CaseworkerActionSignal) -> None:
    await handle.signal("caseworker_action", request)
