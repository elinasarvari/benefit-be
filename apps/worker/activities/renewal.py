"""The redetermination request's activities (007; ADR-007-01, ADR-007-05; FR-007-03, FR-007-13).

`check_renewal` compares the held household data with the reference lookup;
`prepare_renewal_request` builds and holds the pre-filled request for a caseworker's approval;
`deliver_renewal_request` delivers it. All three run until done (TW-10): a lookup outage is
retried and never fails the case (FR-007-13, TC-30). Labels are built here and stored in
`notices`, never returned to the workflow (constitution VIII, TW-4). Every step is idempotent on
its derived IDs (TW-11).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol
from uuid import UUID, uuid5

import structlog
from temporalio import activity
from temporalio.exceptions import ApplicationError

from apps.worker.policies import warn_if_retrying
from apps.worker.repositories.reference_records import ReferenceRecords
from apps.worker.repositories.snapshots import SnapshotSource
from packages.domain.case import CoveragePeriod
from packages.domain.household import HouseholdMember
from packages.domain.notice import RequestedItem
from packages.domain.renewal import (
    RenewalCheck,
    RenewalCheckRequest,
    RenewalRequestDraft,
    RenewalRequestHeld,
    RequestDelivery,
)
from packages.domain.verification import ReferenceAnswer, ReferenceLookup
from packages.platform.clock import Clock
from packages.platform.scope import Scope, ScopeKind
from packages.rules.renewal import renewal_items, requested_items

log = structlog.get_logger()


def renewal_check_id_for(case_id: UUID) -> UUID:
    return uuid5(case_id, "renewal_check")


def request_notice_id_for(case_id: UUID) -> UUID:
    return uuid5(case_id, "request_for_information")


class Periods(Protocol):
    async def find_period(self, scope: Scope, period_id: UUID) -> CoveragePeriod | None: ...


class CaseDates(Protocol):
    async def received_on(self, scope: Scope, case_id: UUID) -> date | None: ...


class RequestNotices(Protocol):
    async def hold_request(
        self, scope: Scope, notice_id: UUID, items: list[RequestedItem], created_at: datetime
    ) -> None: ...

    async def deliver(
        self, scope: Scope, notice_id: UUID, due_case_date: date, delivered_at: datetime
    ) -> bool: ...


class RenewalActivities:
    """Built once per worker process (TW-12) with its stores injected."""

    def __init__(
        self,
        periods: Periods,
        members: SnapshotSource,
        references: ReferenceRecords,
        notices: RequestNotices,
        case_dates: CaseDates,
        clock: Clock,
    ) -> None:
        self._periods = periods
        self._members = members
        self._references = references
        self._notices = notices
        self._case_dates = case_dates
        self._clock = clock

    async def _held_and_answers(
        self, scope: Scope, household_id: UUID, year: int
    ) -> tuple[list[HouseholdMember], list[ReferenceAnswer]]:
        held = await self._members.members_for_year(scope, year)
        answers = [
            await self._references.lookup(
                scope,
                ReferenceLookup(
                    household_id=household_id,
                    member_id=member.member_id,
                    given_name=member.given_name,
                    family_name=member.family_name,
                    date_of_birth=member.date_of_birth,
                ),
            )
            for member in held
        ]
        return held, answers

    @activity.defn(name="check_renewal")
    async def check_renewal(self, request: RenewalCheckRequest) -> RenewalCheck:
        warn_if_retrying()
        scope = Scope.for_worker(request.case_id, request.household_id)
        period = await self._periods.find_period(scope, request.coverage_period_id)
        if period is None:
            raise ApplicationError("coverage period not found", non_retryable=True)
        held, answers = await self._held_and_answers(
            scope, request.household_id, request.as_of_date.year
        )
        items = renewal_items(held, answers)
        log.info(
            "renewal.checked",
            case_id=str(request.case_id),
            workflow_id=activity.info().workflow_id,
            item_count=len(items),
        )
        return RenewalCheck(
            renewal_check_id=renewal_check_id_for(request.case_id),
            case_id=request.case_id,
            household_id=request.household_id,
            coverage_period_id=request.coverage_period_id,
            coverage_end=period.end_date,
            items=items,
            complete=not items,
        )

    @activity.defn(name="prepare_renewal_request")
    async def prepare_renewal_request(self, draft: RenewalRequestDraft) -> RenewalRequestHeld:
        warn_if_retrying()
        scope = Scope.for_worker(draft.case_id, draft.household_id)
        received_on = await self._case_dates.received_on(scope, draft.case_id)
        if received_on is None:
            raise ApplicationError("case not found", non_retryable=True)
        held, answers = await self._held_and_answers(scope, draft.household_id, received_on.year)
        notice_id = request_notice_id_for(draft.case_id)
        items = requested_items(notice_id, draft.items, held, answers, draft.determination_outcome)
        await self._notices.hold_request(scope, notice_id, items, self._clock.now())
        log.info(
            "renewal.request_held",
            case_id=str(draft.case_id),
            workflow_id=activity.info().workflow_id,
            item_count=len(items),
        )
        return RenewalRequestHeld(notice_id=notice_id, item_count=len(items))

    @activity.defn(name="deliver_renewal_request")
    async def deliver_renewal_request(self, delivery: RequestDelivery) -> None:
        warn_if_retrying()
        # `notices` is scoped by case, so the case alone bounds this write (ADR-001-06).
        scope = Scope(ScopeKind.worker, case_id=delivery.case_id)
        delivered = await self._notices.deliver(
            scope, delivery.notice_id, delivery.due_case_date, self._clock.now()
        )
        if not delivered:
            raise ApplicationError("no held request to deliver", non_retryable=True)
        log.info(
            "renewal.request_delivered",
            case_id=str(delivery.case_id),
            workflow_id=activity.info().workflow_id,
        )
