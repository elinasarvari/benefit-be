"""`determine_eligibility`: the real rules engine behind 001's activity (ADR-003-02, TW-9).

Captures the household snapshot (ADR-003-03), evaluates it, stores the full `DeterminationRecord`
(`_id` = `determination_id`, idempotent on a duplicate key) and returns 001's `case.Determination`
summary, mapped per research R5. Name, input and output types are unchanged, so `CaseWorkflow` and
its recorded histories are untouched. The same request always gives the same record and summary
(TW-11): the snapshot is reused and the IDs are derived from the case and the as-of date (R6).
"""

from __future__ import annotations

from uuid import UUID, uuid5

import structlog
from temporalio import activity

from apps.worker.activities.snapshot import SnapshotCapture
from apps.worker.policies import warn_if_retrying
from apps.worker.repositories.determinations import DeterminationStore
from packages.domain.case import (
    DenialReason,
    Determination,
    DeterminationOutcome,
    DeterminationRequest,
)
from packages.domain.eligibility import Determination as FullDetermination
from packages.domain.eligibility import (
    DeterminationRecord,
    RuleId,
    RuleOutcome,
)
from packages.platform.clock import Clock
from packages.platform.scope import Scope
from packages.rules.engine import evaluate

log = structlog.get_logger()


def determination_id_for(request: DeterminationRequest) -> UUID:
    """The stub's formula, so `CaseOverride.determination_id` references stay valid (R6)."""
    return uuid5(request.case_id, f"determination:{request.as_of_date.isoformat()}")


def summarize(determination_id: UUID, full: FullDetermination) -> Determination:
    """001's summary of an engine result (research R5)."""
    failed: list[DenialReason] = []
    for rule in full.rule_results:
        if rule.outcome is not RuleOutcome.fail:
            continue
        reason = (
            DenialReason.income_over_limit
            if rule.rule_id is RuleId.ELIG_06
            else DenialReason.other_rule_not_met
        )
        if reason not in failed:
            failed.append(reason)
    if DenialReason.income_over_limit in failed:
        reason_code: DenialReason | None = DenialReason.income_over_limit
    else:
        reason_code = failed[0] if failed else None
    missing = list(dict.fromkeys(item.item for item in full.missing_items))
    return Determination(
        determination_id=determination_id,
        outcome=DeterminationOutcome(full.result),
        reason_code=reason_code,
        failed_rules=failed,
        missing_items=missing,
        as_of_date=full.as_of_date,
    )


class DeterminationActivities:
    """Built once per worker process (TW-12) with its stores injected."""

    def __init__(self, snapshots: SnapshotCapture, store: DeterminationStore, clock: Clock) -> None:
        self._snapshots = snapshots
        self._store = store
        self._clock = clock

    @activity.defn(name="determine_eligibility")
    async def determine_eligibility(self, request: DeterminationRequest) -> Determination:
        warn_if_retrying()  # UNTIL_DONE: the server retries an outage without limit
        scope = Scope.for_worker(request.case_id, request.household_id)
        snapshot = await self._snapshots.capture(scope, request.as_of_date, request.held_year)
        full = evaluate(snapshot, request.as_of_date)
        determination_id = determination_id_for(request)
        stored = await self._store.insert(
            scope,
            DeterminationRecord(
                determination_id=determination_id,
                household_id=request.household_id,
                case_id=request.case_id,
                snapshot_id=snapshot.snapshot_id,
                recorded_at=self._clock.now(),
                determination=full,
            ),
        )
        log.info(
            "determine_eligibility",
            case_id=str(request.case_id),
            workflow_id=activity.info().workflow_id,
            outcome=full.result.value,
            already_stored=not stored,
        )
        return summarize(determination_id, full)
