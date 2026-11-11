"""`determine_eligibility` stub (001, research R1). 003 replaces the body with its rules (TW-9).

The result follows the household's synthetic stub result (seeded by `db/seeds/generate.py` into
`household_stub_results`, read by `apps/worker/repositories/stub_results.py`), or a test fixture.
It is idempotent (TW-11): the same request always gives the same
determination, including its ID.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol
from uuid import UUID, uuid5

import structlog
from temporalio import activity

from apps.worker.policies import warn_if_retrying
from packages.domain.case import (
    DenialReason,
    Determination,
    DeterminationOutcome,
    DeterminationRequest,
)
from packages.platform.scope import Scope

log = structlog.get_logger()

STUB_MISSING_ITEMS = ["pay_stub"]


class StubResults(Protocol):
    """Reads a household's stub result through the worker scope (ADR-001-06)."""

    async def outcome_for(self, scope: Scope) -> DeterminationOutcome: ...


class FixedStubResults:
    """Stub results by household, failing closed to `cannot_determine` if not listed (R21)."""

    def __init__(
        self,
        by_household: Mapping[UUID, DeterminationOutcome] | None = None,
        default: DeterminationOutcome = DeterminationOutcome.cannot_determine,
    ) -> None:
        self._by_household = dict(by_household or {})
        self._default = default

    async def outcome_for(self, scope: Scope) -> DeterminationOutcome:
        if scope.household_id is None:
            return self._default
        return self._by_household.get(scope.household_id, self._default)


def stub_determination(
    request: DeterminationRequest, outcome: DeterminationOutcome
) -> Determination:
    determination_id = uuid5(request.case_id, f"determination:{request.as_of_date.isoformat()}")
    failed_rules = (
        [DenialReason.income_over_limit] if outcome is DeterminationOutcome.ineligible else []
    )
    return Determination(
        determination_id=determination_id,
        outcome=outcome,
        reason_code=failed_rules[0] if failed_rules else None,
        failed_rules=failed_rules,
        missing_items=(
            list(STUB_MISSING_ITEMS) if outcome is DeterminationOutcome.cannot_determine else []
        ),
        as_of_date=request.as_of_date,
    )


class DeterminationActivities:
    """Built once per worker process (TW-12) with the stub results injected."""

    def __init__(self, results: StubResults) -> None:
        self._results = results

    @activity.defn(name="determine_eligibility")
    async def determine_eligibility(self, request: DeterminationRequest) -> Determination:
        warn_if_retrying()  # UNTIL_DONE: the server retries an outage without limit
        scope = Scope.for_worker(request.case_id, request.household_id)
        outcome = await self._results.outcome_for(scope)
        log.info(
            "determine_eligibility.stub",
            case_id=str(request.case_id),
            workflow_id=activity.info().workflow_id,
            outcome=outcome.value,
        )
        return stub_determination(request, outcome)
