"""Case opening service (001, T019; TC-50–TC-57) with a fake repository, workflows and clock."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date
from typing import TypeVar
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from apps.api.repositories.cases import CaseAlreadyExists
from apps.api.services import cases as service
from packages.domain.auth import Role
from packages.domain.case import (
    ApplicationSubmission,
    Case,
    CaseState,
    CoveragePeriod,
    CoverageStatus,
    SubmissionRefusal,
    case_id_for,
)
from packages.platform.clock import FixedClock
from packages.platform.errors import Conflict, DependencyUnavailable
from packages.platform.scope import Scope
from tests.apps.api.case_fakes import FIXED_NOW, FakeCaseRepository, FakeCaseWorkflows, principal

H1 = UUID("00000000-0000-4000-8000-0000000000a1")
SUB_A = UUID("00000000-0000-4000-8000-00000000000a")
SUB_B = UUID("00000000-0000-4000-8000-00000000000b")
T = TypeVar("T")


@pytest.fixture
def repo() -> FakeCaseRepository:
    return FakeCaseRepository()


@pytest.fixture
def workflows() -> FakeCaseWorkflows:
    return FakeCaseWorkflows()


async def submit(
    repo: FakeCaseRepository,
    workflows: FakeCaseWorkflows,
    reference: UUID = SUB_A,
    *,
    household: UUID = H1,
    demo_profile: bool = False,
) -> service.SubmissionResult:
    return await service.submit_application(
        ApplicationSubmission(submission_reference=reference),
        principal(Role.applicant, household),
        repo=repo,
        workflows=workflows,
        clock=FixedClock(FIXED_NOW),
        demo_profile=demo_profile,
    )


def coverage(end_date: date, household: UUID = H1) -> CoveragePeriod:
    return CoveragePeriod(
        id=uuid4(),
        household_id=household,
        case_id=uuid4(),
        start_date=date(2025, 3, 1),
        end_date=end_date,
        status=CoverageStatus.active,
    )


def refusal_of(error: Conflict) -> str:
    return error.fields[0].type


async def test_FR_001_01_submit_opens_submitted_case_with_deadline_45_days(
    repo: FakeCaseRepository, workflows: FakeCaseWorkflows
) -> None:
    result = await submit(repo, workflows)

    case_id = case_id_for(SUB_A)
    assert result.created is True
    assert result.status.case_id == case_id
    assert result.status.state is CaseState.submitted
    assert result.status.received_on == date(2026, 3, 17)
    assert result.status.decision_deadline == date(2026, 5, 1)
    assert result.status.days_remaining == 45
    assert result.status.at_risk is False
    stored = repo.cases[case_id]
    assert stored.open is True
    assert stored.state is CaseState.submitted
    assert stored.submission_reference == str(SUB_A)
    start = workflows.started[f"case-{case_id}"]
    assert start.case_id == case_id
    assert start.household_id == H1
    assert start.clock == stored.clock


async def test_FR_001_23_same_submission_twice_returns_existing_case(
    repo: FakeCaseRepository, workflows: FakeCaseWorkflows
) -> None:
    first = await submit(repo, workflows)
    second = await submit(repo, workflows)

    assert (first.created, second.created) == (True, False)
    assert second.status == first.status
    assert repo.inserts == 1
    assert len(workflows.started) == 1


async def test_FR_001_23_household_with_open_case_refused_with_reason(
    repo: FakeCaseRepository, workflows: FakeCaseWorkflows
) -> None:
    await submit(repo, workflows, SUB_A)

    with pytest.raises(Conflict) as refused:
        await submit(repo, workflows, SUB_B)

    assert refusal_of(refused.value) == SubmissionRefusal.household_has_open_case.value
    assert case_id_for(SUB_B) not in repo.cases
    assert f"case-{case_id_for(SUB_B)}" not in workflows.started


async def test_FR_001_23_household_with_unended_coverage_refused(
    repo: FakeCaseRepository, workflows: FakeCaseWorkflows
) -> None:
    repo.coverage.append(coverage(date(2026, 3, 17)))

    with pytest.raises(Conflict) as refused:
        await submit(repo, workflows)

    assert refusal_of(refused.value) == SubmissionRefusal.household_has_coverage.value
    assert repo.cases == {}
    assert workflows.start_calls == 0


async def test_FR_001_23_coverage_ended_yesterday_allows_new_application(
    repo: FakeCaseRepository, workflows: FakeCaseWorkflows
) -> None:
    repo.coverage.append(coverage(date(2026, 3, 16)))

    result = await submit(repo, workflows)

    assert result.created is True


async def test_FR_001_01_temporal_down_returns_503_and_retry_starts_workflow(
    repo: FakeCaseRepository, workflows: FakeCaseWorkflows
) -> None:
    workflows.fail_next_start = True

    with pytest.raises(DependencyUnavailable):
        await submit(repo, workflows)
    retried = await submit(repo, workflows)

    assert retried.created is False
    assert retried.status.case_id == case_id_for(SUB_A)
    assert repo.inserts == 1
    assert workflows.start_calls == 2
    assert f"case-{case_id_for(SUB_A)}" in workflows.started


@pytest.mark.parametrize(("demo_profile", "scale"), [(True, 1440), (False, 1)])
async def test_FR_001_13_demo_profile_case_clock_scale_1440(
    repo: FakeCaseRepository, workflows: FakeCaseWorkflows, demo_profile: bool, scale: int
) -> None:
    await submit(repo, workflows, demo_profile=demo_profile)

    clock = repo.cases[case_id_for(SUB_A)].clock
    assert clock.scale == scale
    assert clock.offset_seconds == 0
    assert clock.real_start == FIXED_NOW
    assert clock.case_start == FIXED_NOW


async def test_FR_001_23_household_taken_from_principal_not_body(
    repo: FakeCaseRepository, workflows: FakeCaseWorkflows
) -> None:
    foreign = uuid4()
    with pytest.raises(ValidationError):
        ApplicationSubmission.model_validate(
            {"submission_reference": str(SUB_A), "household_id": str(foreign)}
        )

    await submit(repo, workflows, household=H1)

    stored = repo.cases[case_id_for(SUB_A)]
    assert stored.household_id == H1
    assert workflows.started[f"case-{stored.case_id}"].household_id == H1


class RacingCaseRepository(FakeCaseRepository):
    """A concurrent request with the same reference inserts first (the unique `_id` wins)."""

    def __init__(self, winner_household: UUID) -> None:
        super().__init__()
        self.winner_household = winner_household
        self.winner: Case | None = None

    async def insert_case(self, scope: Scope, case: Case) -> None:
        self.winner = case.model_copy(update={"household_id": self.winner_household})
        raise CaseAlreadyExists()

    async def run_in_transaction(self, work: Callable[[FakeCaseRepository], Awaitable[T]]) -> T:
        try:
            return await super().run_in_transaction(work)
        finally:
            if self.winner is not None:  # the winner's commit survives our rollback
                self.cases[self.winner.id] = self.winner


async def test_FR_001_23_concurrent_same_reference_returns_winning_case(
    workflows: FakeCaseWorkflows,
) -> None:
    repo = RacingCaseRepository(winner_household=H1)

    result = await submit(repo, workflows)

    assert result.created is False
    assert result.status.case_id == case_id_for(SUB_A)
    assert repo.cases[case_id_for(SUB_A)].household_id == H1
    assert workflows.start_calls == 1
    assert f"case-{case_id_for(SUB_A)}" in workflows.started


async def test_FR_001_23_reference_of_another_household_refused_with_reason(
    repo: FakeCaseRepository, workflows: FakeCaseWorkflows
) -> None:
    other_household = UUID("00000000-0000-4000-8000-0000000000a2")
    await submit(repo, workflows, SUB_A, household=other_household)
    started = workflows.start_calls

    with pytest.raises(Conflict) as refused:
        await submit(repo, workflows, SUB_A, household=H1)

    assert refusal_of(refused.value) == SubmissionRefusal.submission_reference_in_use.value
    assert refused.value.fields[0].field == "submission_reference"
    assert repo.cases[case_id_for(SUB_A)].household_id == other_household  # never revealed
    assert workflows.start_calls == started
