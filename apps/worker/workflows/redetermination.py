"""The redetermination path of `CaseWorkflow` (007; ADR-007-01, ADR-007-02 B, ADR-007-03/04).

`CaseWorkflow.run` hands a `redetermination` case to `RedeterminationPath.run` behind one
`workflow.patched` call, so recorded application histories replay unchanged (TW-7, TW-14).

verifying (check_renewal) -> determining -> decided (renewed automatically), or the request is
held for a caseworker's `approve` (task `notice_approval`), delivered, and the case waits in
`awaiting_applicant` for the `applicant_response` signal or 30 case days. A response is verified
and determined again; no response, or a result that is not eligible, opens a review task.
Coverage never ends here: only `approve_denial` ends it. When the coverage end passes during the
wait or review, the prior period is `continued_pending_review` and the open task is escalated.

History holds codes, IDs and counts only; the request's labels are built and stored by the
activity (constitution VIII).
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid5

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from apps.worker.policies import AGENT_STEP, UNTIL_DONE
    from apps.worker.review_payloads import ReviewFlagUpdate
    from apps.worker.workflows.calendar import (
        case_now_wf,
        chicago_date,
        until_case_instant,
        until_next_case_day,
    )
    from apps.worker.workflows.case_states import denial_reason_for
    from apps.worker.workflows.review import DETERMINATION_REF_KIND
    from apps.worker.workflows.review_timers import coverage_end_passes
    from packages.domain.case import (
        CaseEventKind,
        CaseOutcome,
        CaseOverride,
        CaseState,
        CaseUpdate,
        CoveragePeriod,
        CoverageStatus,
        CoverageStatusChange,
        Determination,
        DeterminationOutcome,
        DeterminationRequest,
        NoticeType,
    )
    from packages.domain.renewal import (
        RenewalCheck,
        RenewalCheckRequest,
        RenewalRequestDraft,
        RenewalRequestHeld,
        RequestDelivery,
    )
    from packages.domain.review import (
        BLOCKING,
        ActionType,
        CaseworkerActionSignal,
        EvidenceRef,
        RejectionCode,
        ReviewCause,
        ReviewFlag,
        ReviewReason,
        ReviewTask,
        ReviewTaskStatus,
    )
    from packages.domain.verification import (
        FindingCause,
        VerificationRequest,
        VerificationResult,
    )
    from packages.domain.waits import (
        REDETERMINATION_RESPONSE_DAYS,
        ApplicantResponseSignal,
        PendingTimer,
        TimerName,
    )

if TYPE_CHECKING:
    from apps.worker.workflows.case import CaseWorkflow

PATCH_ID = "007-redetermination-path"
CHECKED = "renewal_checked"
RESPONSE_RECEIVED = "applicant_response"
RESPONSE_DEADLINE_MISSED = "response_deadline_missed"
COVERAGE_END_PASSED = "coverage_end_passed"
NOTICE_REF_KIND = "notice_id"
RENEWAL_CHECK_REF_KIND = "renewal_check_id"

_FINDING_CAUSES = (
    (FindingCause.identity_mismatch, ReviewCause.identity_mismatch),
    (FindingCause.different_person, ReviewCause.identity_mismatch),
    (FindingCause.conflicting_stubs, ReviewCause.conflicting_stubs),
    (FindingCause.income_mismatch, ReviewCause.income_mismatch),
)


def renewal_period(prior_end: date) -> tuple[date, date]:
    """The renewed period starts the day after the old one ends and lasts 12 calendar months
    (FR-007-05, research R6)."""
    start = prior_end + timedelta(days=1)
    year, month = start.year + 1, start.month
    day = min(start.day, calendar.monthrange(year, month)[1])
    return start, date(year, month, day) - timedelta(days=1)


def review_cause_for(result: VerificationResult) -> ReviewCause:
    """The first open finding's cause, identity first (research R4)."""
    open_causes = {f.cause for f in result.findings if f.dismissed is None}
    for finding_cause, review_cause in _FINDING_CAUSES:
        if finding_cause in open_causes:
            return review_cause
    return ReviewCause.cannot_determine


class RedeterminationPath:
    """Steps of one redetermination, run by the `CaseWorkflow` that owns the state, the sequence
    numbers and the signal queues."""

    def __init__(self, case: CaseWorkflow) -> None:
        self._case = case
        self._check: RenewalCheck | None = None
        self._notice_id: UUID | None = None
        self._response_due: datetime | None = None  # case instant the 30 days end
        self._coverage_continued = False

    # --- Run ---------------------------------------------------------------------------------

    async def run(self) -> None:
        case = self._case
        start = case._start
        await case._move(CaseState.verifying, "workflow_started")
        # UNTIL_DONE: a lookup outage is retried, never fails the case (TW-10, FR-007-13).
        check: RenewalCheck = await workflow.execute_activity(
            "check_renewal",
            RenewalCheckRequest(
                case_id=start.case_id,
                household_id=start.household_id,
                coverage_period_id=_period_id(case),
                as_of_date=case._received_on,
            ),
            result_type=RenewalCheck,
            **UNTIL_DONE,
        )
        self._check = check
        case._deadline = check.coverage_end  # no 45-day clock (FR-007-08, ADR-007-03)
        await case._move(CaseState.determining, CHECKED)
        determination = await self._determine(case._received_on)
        if determination.outcome is DeterminationOutcome.eligible and check.complete:
            await self._renew(CaseOutcome.renewed_automatically, NoticeType.automatic_renewal)
            return
        await self._hold_request(determination)
        while case._state is not CaseState.decided:
            if case._state is CaseState.under_review:
                await self._wait_in_review()
            else:
                await self._wait_for_response()

    async def _determine(self, as_of_date: date) -> Determination:
        case = self._case
        determination: Determination = await workflow.execute_activity(
            "determine_eligibility",
            DeterminationRequest(
                case_id=case._start.case_id,
                household_id=case._start.household_id,
                as_of_date=as_of_date,
                held_year=case._received_on.year,
            ),
            result_type=Determination,
            **UNTIL_DONE,
        )
        case._determination = determination
        return determination

    # --- Waits -------------------------------------------------------------------------------

    def pending_timer(self) -> PendingTimer | None:
        """The timer "Advance clock" would fire: the earlier of the response deadline and the
        coverage end (ADR-007-04). Nothing is pending once the case is decided."""
        case = self._case
        if self._check is None or case._state not in (
            CaseState.under_review,
            CaseState.awaiting_applicant,
        ):
            return None
        timers: list[PendingTimer] = []
        if case._state is CaseState.awaiting_applicant and self._response_due is not None:
            timers.append(
                PendingTimer(
                    name=TimerName.redetermination_response_deadline,
                    due_case_time=self._response_due,
                )
            )
        if not self._coverage_continued:
            timers.append(
                PendingTimer(
                    name=TimerName.coverage_end,
                    due_case_time=coverage_end_passes(self._check.coverage_end),
                )
            )
        return min(timers, key=lambda timer: timer.due_case_time) if timers else None

    def _wake_in(self) -> timedelta:
        """Wake at each new case day (so `status` counts from now) and at each due instant."""
        case = self._case
        clock = case._start.clock
        wake = until_next_case_day(clock)
        for timer in self._due_instants():
            wake = min(wake, until_case_instant(clock, timer))
        return wake

    def _due_instants(self) -> list[datetime]:
        due: list[datetime] = []
        if self._check is not None and not self._coverage_continued:
            due.append(coverage_end_passes(self._check.coverage_end))
        if self._case._state is CaseState.awaiting_applicant and self._response_due is not None:
            due.append(self._response_due)
        return due

    async def _wait_in_review(self) -> None:
        case = self._case
        try:
            await workflow.wait_condition(lambda: bool(case._pending), timeout=self._wake_in())
        except TimeoutError:
            await self._on_wake()
            return
        await self._apply_next()

    async def _wait_for_response(self) -> None:
        case = self._case
        try:
            await workflow.wait_condition(
                lambda: bool(case._pending) or bool(case._responses), timeout=self._wake_in()
            )
        except TimeoutError:
            await self._on_wake()
            return
        if case._pending:  # a caseworker action with no open task is refused and recorded
            await self._apply_next()
        elif case._responses:
            await self._handle_response(case._responses.pop(0))

    async def _on_wake(self) -> None:
        case = self._case
        check = self._check
        if check is None:
            return
        now = case_now_wf(case._start.clock)
        if not self._coverage_continued and now >= coverage_end_passes(check.coverage_end):
            await self._continue_coverage()
        if (
            case._state is CaseState.awaiting_applicant
            and self._response_due is not None
            and now >= self._response_due
        ):
            await self._open_review(
                ReviewReason.cannot_determine,
                ReviewCause.response_deadline_missed,
                RESPONSE_DEADLINE_MISSED,
            )

    # --- Coverage end (ADR-007-04) -----------------------------------------------------------

    async def _continue_coverage(self) -> None:
        """Coverage never ends on its own: it continues and the open review is escalated."""
        case = self._case
        self._coverage_continued = True
        event = case._event(CaseEventKind.coverage_continued, COVERAGE_END_PASSED)
        await case._record(
            event,
            prior_coverage=CoverageStatusChange(
                coverage_period_id=_period_id(case),
                household_id=case._start.household_id,
                status=CoverageStatus.continued_pending_review,
            ),
        )
        task = case._open_task()
        if task is not None:
            await self._escalate(task, event.case_time)

    async def _escalate(self, task: ReviewTask, at_case: datetime) -> None:
        case = self._case
        await workflow.execute_activity(
            "set_review_flag",
            ReviewFlagUpdate(
                household_id=task.household_id,
                case_id=task.case_id,
                task_id=task.task_id,
                flag=ReviewFlag.escalated,
                at_case=at_case,
            ),
            **UNTIL_DONE,
        )
        case._task = task.model_copy(
            update={"flags": [*task.flags, ReviewFlag.escalated], "escalated_at": at_case}
        )

    # --- The request ---------------------------------------------------------------------------

    async def _hold_request(self, determination: Determination) -> None:
        case = self._case
        check = self._required_check()
        held: RenewalRequestHeld = await workflow.execute_activity(
            "prepare_renewal_request",
            RenewalRequestDraft(
                case_id=case._start.case_id,
                household_id=case._start.household_id,
                renewal_check_id=check.renewal_check_id,
                determination_outcome=determination.outcome,
                items=check.items,
            ),
            result_type=RenewalRequestHeld,
            **UNTIL_DONE,
        )
        self._notice_id = held.notice_id
        await self._open_review(
            ReviewReason.notice_approval, ReviewCause.notice_ready, "notice_ready"
        )

    async def _approve_request(self, request: CaseworkerActionSignal) -> None:
        case = self._case
        task = case._required_task()
        notice_id = self._notice_id
        if notice_id is None:
            raise RuntimeError("no request is held")
        # The 30 days run on the case clock, so the demo time scale applies (FR-007-11).
        due_at = case_now_wf(case._start.clock) + timedelta(days=REDETERMINATION_RESPONSE_DAYS)
        await workflow.execute_activity(
            "deliver_renewal_request",
            RequestDelivery(
                case_id=case._start.case_id,
                notice_id=notice_id,
                due_case_date=chicago_date(due_at),
            ),
            **UNTIL_DONE,
        )
        event = case._event(
            CaseEventKind.state_changed,
            request.action.value,
            to_state=CaseState.awaiting_applicant,
            actor_id=request.reviewer_id,
            review_task_id=task.task_id,
        )
        self._response_due = event.case_time + timedelta(days=REDETERMINATION_RESPONSE_DAYS)
        resolved = case._closed_task(request, event.real_time)
        case._task = resolved
        await case._record(
            event,
            case_update=CaseUpdate(state=CaseState.awaiting_applicant),
            review_task=resolved,
            action=case._action_record(request, event),
        )

    # --- The response ----------------------------------------------------------------------------

    async def _handle_response(self, response: ApplicantResponseSignal) -> None:
        case = self._case
        if response.notice_id != self._notice_id:
            event = case._event(
                CaseEventKind.action_rejected, RejectionCode.not_allowed_for_reason.value
            )
            await case._record(event)
            return
        await case._move(CaseState.verifying, RESPONSE_RECEIVED)
        result: VerificationResult = await workflow.execute_activity(
            "verify_documents",
            VerificationRequest(
                case_id=case._start.case_id,
                household_id=case._start.household_id,
                as_of_date=case._received_on,
            ),
            result_type=VerificationResult,
            **AGENT_STEP,
        )
        await case._move(CaseState.determining, "verification_complete")
        # A snapshot is stored once per (case, date): a later date makes the determination read
        # what the applicant confirmed, not the one the request was built on (research R14).
        as_of = max(
            chicago_date(case_now_wf(case._start.clock)), case._received_on + timedelta(days=1)
        )
        determination = await self._determine(as_of)
        if determination.outcome is DeterminationOutcome.eligible and not result.needs_review:
            await self._renew(CaseOutcome.renewed_after_response, NoticeType.renewal_after_response)
        elif result.needs_review:
            await self._open_review(
                ReviewReason.cannot_determine, review_cause_for(result), "needs_review"
            )
        elif determination.outcome is DeterminationOutcome.ineligible:
            await self._open_review(
                ReviewReason.adverse_determination,
                ReviewCause.ineligible,
                determination.outcome.value,
            )
        else:
            await self._open_review(
                ReviewReason.cannot_determine,
                ReviewCause.cannot_determine,
                determination.outcome.value,
            )

    # --- Review ----------------------------------------------------------------------------------

    async def _open_review(
        self, reason: ReviewReason, cause: ReviewCause, reason_code: str
    ) -> None:
        case = self._case
        check = self._required_check()
        determination = case._determination
        task_id = workflow.uuid4()
        event = case._event(
            CaseEventKind.state_changed,
            reason_code,
            to_state=CaseState.under_review,
            review_task_id=task_id,
        )
        evidence = [EvidenceRef(kind=RENEWAL_CHECK_REF_KIND, id=check.renewal_check_id)]
        if determination is not None:
            evidence.insert(
                0, EvidenceRef(kind=DETERMINATION_REF_KIND, id=determination.determination_id)
            )
        if self._notice_id is not None:
            evidence.append(EvidenceRef(kind=NOTICE_REF_KIND, id=self._notice_id))
        escalated = self._coverage_continued
        task = ReviewTask(
            task_id=task_id,
            household_id=case._start.household_id,
            case_id=case._start.case_id,
            workflow_id=workflow.info().workflow_id,
            reason=reason,
            cause=cause,
            evidence_refs=evidence,
            blocking=reason in BLOCKING,
            status=ReviewTaskStatus.open,
            opened_at_case=event.case_time,
            opened_at_real=event.real_time,
            decision_deadline=check.coverage_end,
            coverage_end=check.coverage_end,
            flags=[ReviewFlag.escalated] if escalated else [],
            escalated_at=event.case_time if escalated else None,
            created_at=event.real_time,
            updated_at=event.real_time,
        )
        case._task = task
        await case._record(
            event, case_update=CaseUpdate(state=CaseState.under_review), review_task=task
        )

    async def _apply_next(self) -> None:
        case = self._case
        request = case._pending.pop(0)
        rejection = case._rejection(request)
        if rejection is not None:
            await case._reject(request, rejection)
        elif request.action is ActionType.approve:
            await self._approve_request(request)
        elif request.action is ActionType.approve_denial:
            await self._end_coverage(request)
        else:
            await self._renew(
                CaseOutcome.renewed_after_response, NoticeType.renewal_after_response, request
            )

    # --- Decisions -----------------------------------------------------------------------------

    def _required_check(self) -> RenewalCheck:
        if self._check is None:
            raise RuntimeError("the renewal check has not run")
        return self._check

    async def _renew(
        self,
        outcome: CaseOutcome,
        notice_type: NoticeType,
        request: CaseworkerActionSignal | None = None,
    ) -> None:
        """A new period from the day after the old end; the old period becomes `renewed` in the
        same transaction (ADR-007-03)."""
        case = self._case
        check = self._required_check()
        event = case._event(
            CaseEventKind.state_changed,
            request.action.value if request is not None else outcome.value,
            to_state=CaseState.decided,
            actor_id=request.reviewer_id if request is not None else None,
            review_task_id=case._task.task_id if request is not None and case._task else None,
        )
        start, end = renewal_period(check.coverage_end)
        parts: dict[str, object] = {}
        override = None
        if request is not None:
            determination = case._determination
            override = CaseOverride(
                reviewer_id=request.reviewer_id,
                reason=request.reason or "",
                determination_id=determination.determination_id if determination else None,
            )
            resolved = case._closed_task(request, event.real_time)
            case._task = resolved
            parts["review_task"] = resolved
            parts["action"] = case._action_record(request, event)
        case._outcome = outcome
        await case._record(
            event,
            case_update=CaseUpdate(
                state=CaseState.decided,
                open=False,
                outcome=outcome,
                override=override,
                decided_on=chicago_date(event.case_time),
            ),
            coverage=CoveragePeriod(
                id=uuid5(case._start.case_id, "coverage"),
                household_id=case._start.household_id,
                case_id=case._start.case_id,
                start_date=start,
                end_date=end,
                status=CoverageStatus.active,
                renews_coverage_period_id=check.coverage_period_id,
            ),
            prior_coverage=CoverageStatusChange(
                coverage_period_id=check.coverage_period_id,
                household_id=case._start.household_id,
                status=CoverageStatus.renewed,
            ),
            notice=case._notice(notice_type, event.case_time),
            **parts,
        )

    async def _end_coverage(self, request: CaseworkerActionSignal) -> None:
        """The only way coverage ends: a caseworker's `approve_denial` (FR-007-12, FR-007-04)."""
        case = self._case
        task = case._required_task()
        determination = case._determination
        failed: list[str] = []
        if determination is not None:
            failed = [rule.value for rule in determination.failed_rules] or (
                [determination.reason_code.value] if determination.reason_code else []
            )
        denial_reason = denial_reason_for(task.reason, failed)
        event = case._event(
            CaseEventKind.state_changed,
            request.action.value,
            to_state=CaseState.decided,
            actor_id=request.reviewer_id,
            review_task_id=task.task_id,
        )
        resolved = case._closed_task(request, event.real_time)
        case._task = resolved
        case._outcome = CaseOutcome.ended_by_caseworker
        case._denial_reason = denial_reason
        await case._record(
            event,
            case_update=CaseUpdate(
                state=CaseState.decided,
                open=False,
                outcome=CaseOutcome.ended_by_caseworker,
                denial_reason=denial_reason,
                decided_on=chicago_date(event.case_time),
            ),
            review_task=resolved,
            action=case._action_record(request, event),
            prior_coverage=CoverageStatusChange(
                coverage_period_id=self._required_check().coverage_period_id,
                household_id=case._start.household_id,
                status=CoverageStatus.ended,
            ),
            notice=case._notice(NoticeType.coverage_ended, event.case_time),
        )


def _period_id(case: CaseWorkflow) -> UUID:
    period_id = case._start.coverage_period_id
    if period_id is None:  # CaseStart already refuses a redetermination without one
        raise RuntimeError("a redetermination needs its coverage period")
    return period_id
