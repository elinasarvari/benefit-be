"""The household page (007a, FR-007-18): members, coverage periods and cases for staff."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from apps.api.schemas.mvp import CoveragePeriodView, HouseholdMemberView, HouseholdView
from apps.api.services.cases import summary_of
from packages.domain.auth import Principal
from packages.domain.case import Case, CaseType, CoveragePeriod
from packages.platform.clock import Clock
from packages.platform.errors import NotFound
from packages.platform.scope import Scope


class HouseholdRepository(Protocol):
    async def members_of(self, scope: Scope, household_id: UUID) -> list[HouseholdMemberView]: ...

    async def coverage_periods_of(
        self, scope: Scope, household_id: UUID
    ) -> list[CoveragePeriod]: ...

    async def find_period(
        self, scope: Scope, household_id: UUID, period_id: UUID
    ) -> CoveragePeriod | None: ...

    async def cases_of(self, scope: Scope, household_id: UUID) -> list[Case]: ...


def _running(cases: list[Case]) -> dict[UUID, UUID]:
    """Coverage period -> its open redetermination case."""
    return {
        c.coverage_period_id: c.case_id
        for c in cases
        if c.case_type is CaseType.redetermination and c.open and c.coverage_period_id is not None
    }


async def get_household(
    household_id: UUID, principal: Principal, *, repo: HouseholdRepository, clock: Clock
) -> HouseholdView:
    """404 when the household has no members, so staff learn nothing beyond what exists."""
    scope = Scope.for_principal(principal)
    members = await repo.members_of(scope, household_id)
    if not members:
        raise NotFound()
    periods = await repo.coverage_periods_of(scope, household_id)
    cases = await repo.cases_of(scope, household_id)
    running = _running(cases)
    now = clock.now()
    return HouseholdView(
        household_id=household_id,
        members=members,
        coverage_periods=[
            CoveragePeriodView(
                coverage_period_id=p.id,
                start_date=p.start_date,
                end_date=p.end_date,
                status=p.status,
                running_redetermination_case_id=running.get(p.id),
            )
            for p in periods
        ],
        cases=[summary_of(c, now) for c in cases],
    )
