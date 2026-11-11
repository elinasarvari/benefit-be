"""RedeterminationsDueWorkflow and its activity (test-plan TC-34, TC-35). Synthetic data only."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from temporalio import workflow
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from apps.worker.activities.redeterminations_due import DueRedeterminationActivities
from apps.worker.repositories.due_cases import Opened, OpenResult
from apps.worker.workflows.redeterminations_due import RedeterminationsDueWorkflow
from packages.domain.case import (
    CaseClock,
    CaseStart,
    CaseType,
    CoveragePeriod,
    CoverageStatus,
    redetermination_case_id,
)
from packages.domain.renewal import DueRedeterminationsRequest
from packages.platform.clock import Clock

NOW = datetime(2026, 5, 1, 11, 0, tzinfo=UTC)
TODAY = date(2026, 5, 1)
HOUSEHOLD = uuid.UUID("00000000-0000-4000-8000-0000000000e1")
QUEUE = "test-due"
started: list[uuid.UUID] = []


@workflow.defn(name="CaseWorkflow")
class StubCaseWorkflow:
    @workflow.run
    async def run(self, start: CaseStart) -> None:
        started.append(start.case_id)


class FixedClock(Clock):
    def now(self) -> datetime:
        return NOW


def period(days_to_end: int) -> CoveragePeriod:
    return CoveragePeriod.model_validate(
        {
            "_id": uuid.uuid4(),
            "household_id": uuid.uuid4(),
            "case_id": uuid.uuid4(),
            "start_date": TODAY - timedelta(days=300),
            "end_date": TODAY + timedelta(days=days_to_end),
            "status": CoverageStatus.active,
        }
    )


class FakePeriods:
    def __init__(self, periods: list[CoveragePeriod]) -> None:
        self.periods = periods

    async def due_periods(self, as_of: date, lead_days: int, limit: int) -> list[CoveragePeriod]:
        cutoff = as_of + timedelta(days=lead_days)
        return [p for p in self.periods if p.end_date <= cutoff][:limit]


class FakeCases:
    def __init__(self, existing: set[uuid.UUID] | None = None) -> None:
        self.existing = existing or set()

    async def open_case(self, period: CoveragePeriod, now: datetime, scale: int) -> OpenResult:
        case_id = redetermination_case_id(period.household_id, period.id)
        start = CaseStart(
            case_id=case_id,
            household_id=period.household_id,
            case_type=CaseType.redetermination,
            submission_reference=str(period.id),
            received_at=now,
            clock=CaseClock(real_start=now, case_start=now, scale=scale),
            coverage_period_id=period.id,
        )
        opened = Opened.existing if case_id in self.existing else Opened.created
        return OpenResult(opened, start)


async def run_due(client: Client, periods: list[CoveragePeriod], cases: FakeCases) -> int:
    activities = DueRedeterminationActivities(FakePeriods(periods), cases, FixedClock(), False)
    async with Worker(
        client,
        task_queue=QUEUE,
        workflow_runner=UnsandboxedWorkflowRunner(),  # the stub records into this module
        workflows=[RedeterminationsDueWorkflow, StubCaseWorkflow],
        activities=[activities.open_due_redeterminations],
    ):
        return await client.execute_workflow(
            RedeterminationsDueWorkflow.run,
            DueRedeterminationsRequest(as_of_date=TODAY),
            id=f"due-{uuid.uuid4()}",
            task_queue=QUEUE,
        )


async def test_FR_007_01_due_workflow_starts_periods_ending_within_60_days() -> None:
    started.clear()
    periods = [period(59), period(60), period(61)]
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        count = await run_due(env.client, periods, FakeCases())

    assert count == 2
    assert sorted(started) == sorted(
        redetermination_case_id(p.household_id, p.id) for p in periods[:2]
    )


async def test_FR_007_01_due_workflow_already_started_case_is_success() -> None:
    started.clear()
    due = period(30)
    case_id = redetermination_case_id(due.household_id, due.id)
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        await env.client.start_workflow(
            "CaseWorkflow",
            CaseStart(
                case_id=case_id,
                household_id=due.household_id,
                case_type=CaseType.redetermination,
                submission_reference=str(due.id),
                received_at=NOW,
                clock=CaseClock(real_start=NOW, case_start=NOW, scale=1),
                coverage_period_id=due.id,
            ),
            id=f"case-{case_id}",
            task_queue="elsewhere",
        )
        count = await run_due(env.client, [due], FakeCases({case_id}))

    assert count == 0
    assert started == []


async def test_FR_007_01_due_workflow_never_restarts_a_case_whose_workflow_closed() -> None:
    """A decided case's closed workflow must not run again if the scan still returns it."""
    started.clear()
    due = period(30)
    case_id = redetermination_case_id(due.household_id, due.id)
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        first = await run_due(env.client, [due], FakeCases())
        second = await run_due(env.client, [due], FakeCases({case_id}))

    assert (first, second) == (1, 0)
    assert started == [case_id]
