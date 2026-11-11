"""Redetermination path of CaseWorkflow, time-skipping, activities stubbed (test-plan TC-16–TC-33).

All data is synthetic. Households E (renews automatically) and F (out-of-date income) are the
seeded demo cases these stand in for.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from temporalio import workflow

from apps.worker.workflows.calendar import chicago_date
from apps.worker.workflows.case import CaseWorkflow
from apps.worker.workflows.redetermination import renewal_period
from packages.domain.case import (
    CaseEventKind,
    CaseOutcome,
    CaseState,
    CoverageStatus,
    DenialReason,
    NoticeType,
)
from packages.domain.review import ActionType, RejectionCode, ReviewCause, ReviewReason
from packages.domain.waits import REDETERMINATION_RESPONSE_DAYS, TimerName
from tests.apps.worker.workflows.case_harness import (
    CANNOT_DETERMINE,
    ELIGIBLE,
    INELIGIBLE,
    RECEIVED_ON,
    SYNTH_REASON,
    action,
    environment,
    signal,
    status_of,
    wait_for_state,
)
from tests.apps.worker.workflows.redetermination_harness import (
    OUT_OF_DATE,
    PERIOD_END,
    PERIOD_ID,
    RenewalStubs,
    reach_awaiting_applicant,
    response,
    send_response,
    start,
    worker,
)

A = ActionType


async def _open_task_id(handle):  # type: ignore[no-untyped-def]
    status = await status_of(handle)
    assert status.open_review_task is not None
    return status.open_review_task.task_id


def _decided(stubs: RenewalStubs):  # type: ignore[no-untyped-def]
    [change] = [c for c in stubs.changes if c.event.to_state is CaseState.decided]
    return change


async def test_FR_007_02_eligible_nothing_missing_renews_automatically() -> None:
    stubs = RenewalStubs(result=ELIGIBLE)
    async with environment() as env, worker(env.client, stubs):
        handle, data = await start(env, stubs)
        final = await handle.result()

    assert final.state is CaseState.decided
    assert final.outcome is CaseOutcome.renewed_automatically
    assert all(change.review_task is None for change in stubs.changes)
    notice = _decided(stubs).notice
    assert notice is not None and notice.notice_type is NoticeType.automatic_renewal
    assert stubs.checks[0].coverage_period_id == PERIOD_ID
    assert stubs.checks[0].case_id == data.case_id
    assert stubs.states() == ["verifying", "determining", "decided"]


def test_FR_007_05_renewal_period_is_twelve_months_from_the_day_after() -> None:
    assert renewal_period(date(2027, 3, 31)) == (date(2027, 4, 1), date(2028, 3, 31))
    assert renewal_period(date(2027, 2, 28)) == (date(2027, 3, 1), date(2028, 2, 29))
    assert renewal_period(date(2027, 1, 30)) == (date(2027, 1, 31), date(2028, 1, 30))


async def test_FR_007_05_renewal_starts_day_after_old_end_for_twelve_months() -> None:
    stubs = RenewalStubs(result=ELIGIBLE, period_end=date(2027, 3, 31))
    async with environment() as env, worker(env.client, stubs):
        handle, data = await start(env, stubs)
        await handle.result()

    change = _decided(stubs)
    assert change.coverage is not None
    assert (change.coverage.start_date, change.coverage.end_date) == (
        date(2027, 4, 1),
        date(2028, 3, 31),
    )
    assert change.coverage.renews_coverage_period_id == PERIOD_ID
    assert change.coverage.status is CoverageStatus.active
    assert change.prior_coverage is not None
    assert change.prior_coverage.coverage_period_id == PERIOD_ID
    assert change.prior_coverage.status is CoverageStatus.renewed
    assert change.prior_coverage.household_id == data.household_id  # one transaction, one household


async def test_FR_007_08_redetermination_has_no_45_day_deadline() -> None:
    stubs = RenewalStubs(result=ELIGIBLE, items=OUT_OF_DATE)
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env, stubs)
        held = await wait_for_state(handle, CaseState.under_review)
        await handle.terminate("test done")

    assert held.decision_deadline == PERIOD_END
    assert held.decision_deadline != RECEIVED_ON + timedelta(days=45)
    assert held.pending_timer is not None
    assert held.pending_timer.name is TimerName.coverage_end


async def test_FR_007_03_out_of_date_income_holds_request_for_approval() -> None:
    stubs = RenewalStubs(result=ELIGIBLE, items=OUT_OF_DATE)
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env, stubs)
        held = await wait_for_state(handle, CaseState.under_review)
        allowed = await handle.query(CaseWorkflow.allowed_actions)
        await handle.terminate("test done")

    task = held.open_review_task
    assert task is not None and task.reason is ReviewReason.notice_approval
    assert allowed.actions == [A.approve]
    assert stubs.drafts[0].items == OUT_OF_DATE  # codes and IDs only (TW-4)
    assert stubs.deliveries == []  # held until a caseworker approves
    assert held.pending_timer is None or (
        held.pending_timer.name is not TimerName.redetermination_response_deadline
    )
    opened = [c.review_task for c in stubs.changes if c.review_task is not None]
    assert opened[0] is not None and opened[0].cause is ReviewCause.notice_ready


async def test_FR_007_11_approve_delivers_request_and_starts_30_day_case_clock_wait() -> None:
    stubs = RenewalStubs(result=ELIGIBLE, items=OUT_OF_DATE)
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env, stubs, scale=1440)
        notice_id = await reach_awaiting_applicant(handle, stubs)
        waiting = await status_of(handle)
        await handle.terminate("test done")

    approved = [c for c in stubs.changes if c.action is not None][0]
    assert waiting.state is CaseState.awaiting_applicant
    assert waiting.open_review_task is None
    assert stubs.deliveries[0].notice_id == notice_id
    assert stubs.deliveries[0].due_case_date == chicago_date(
        approved.event.case_time + timedelta(days=REDETERMINATION_RESPONSE_DAYS)
    )
    assert waiting.pending_timer is not None
    assert waiting.pending_timer.name is TimerName.redetermination_response_deadline
    assert waiting.pending_timer.due_case_time == approved.event.case_time + timedelta(
        days=REDETERMINATION_RESPONSE_DAYS
    )
    assert approved.review_task is not None and approved.review_task.closed_by_action_id


async def test_FR_007_04_response_runs_verification_then_renews_after_response() -> None:
    stubs = RenewalStubs(result=ELIGIBLE, items=OUT_OF_DATE)
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env, stubs)
        notice_id = await reach_awaiting_applicant(handle, stubs)
        await send_response(handle, response(notice_id))
        final = await handle.result()

    assert final.outcome is CaseOutcome.renewed_after_response
    assert stubs.verify_calls == 1
    assert stubs.states()[-3:] == ["verifying", "determining", "decided"]
    first, second = (call.as_of_date for call in stubs.determine_calls)
    assert second > first  # a new snapshot, not the one the request was built on
    change = _decided(stubs)
    assert change.notice is not None
    assert change.notice.notice_type is NoticeType.renewal_after_response
    assert change.prior_coverage is not None
    assert change.prior_coverage.status is CoverageStatus.renewed


async def test_FR_007_04_every_determination_reads_the_held_data_of_the_received_year() -> None:
    stubs = RenewalStubs(result=ELIGIBLE, items=OUT_OF_DATE)
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env, stubs)
        notice_id = await reach_awaiting_applicant(handle, stubs)
        await send_response(handle, response(notice_id))
        await handle.result()

    assert {call.held_year for call in stubs.determine_calls} == {RECEIVED_ON.year}


async def test_FR_007_03_response_with_other_notice_id_is_dropped_and_recorded() -> None:
    stubs = RenewalStubs(result=ELIGIBLE, items=OUT_OF_DATE)
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env, stubs)
        await wait_for_state(handle, CaseState.under_review)
        early = len(stubs.changes)
        await send_response(handle, response(stubs.notice_id()))  # nothing is waiting for it
        await handle.signal("applicant_response", {"notice_id": "not-a-uuid"})  # malformed
        await asyncio.sleep(0.3)
        assert len(stubs.changes) == early
        await reach_awaiting_applicant(handle, stubs)
        before = len(stubs.changes)
        other = response(PERIOD_ID)  # not the delivered request
        await send_response(handle, other)
        for _ in range(100):
            if len(stubs.changes) > before:
                break
            await asyncio.sleep(0.05)
        still = await status_of(handle)
        await handle.terminate("test done")

    assert still.state is CaseState.awaiting_applicant
    rejected = stubs.changes[before].event
    assert rejected.kind is CaseEventKind.action_rejected
    assert rejected.reason_code == RejectionCode.not_allowed_for_reason.value
    assert stubs.verify_calls == 0


async def test_FR_007_04_out_of_date_income_opens_review_coverage_continues() -> None:
    stubs = RenewalStubs(result=ELIGIBLE, items=OUT_OF_DATE)
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env, stubs)
        await reach_awaiting_applicant(handle, stubs)
        await env.sleep(timedelta(days=REDETERMINATION_RESPONSE_DAYS + 1))
        review = await wait_for_state(handle, CaseState.under_review)
        await handle.terminate("test done")

    task = review.open_review_task
    assert task is not None and task.reason is ReviewReason.cannot_determine
    opened = [c.review_task for c in stubs.changes if c.review_task is not None][-1]
    assert opened is not None and opened.cause is ReviewCause.response_deadline_missed
    assert all(
        c.prior_coverage is None or c.prior_coverage.status is not CoverageStatus.ended
        for c in stubs.changes
    )
    assert review.outcome is None


async def test_FR_007_06_adverse_outcome_waits_for_caseworker_approval() -> None:
    stubs = RenewalStubs(result=ELIGIBLE, items=OUT_OF_DATE, after_response=INELIGIBLE)
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env, stubs)
        notice_id = await reach_awaiting_applicant(handle, stubs)
        await send_response(handle, response(notice_id))
        review = await wait_for_state(handle, CaseState.under_review)
        await env.sleep(timedelta(days=3))
        still = await status_of(handle)
        await handle.terminate("test done")

    assert review.open_review_task is not None
    assert review.open_review_task.reason is ReviewReason.adverse_determination
    assert still.state is CaseState.under_review and still.outcome is None
    assert all(
        c.prior_coverage is None or c.prior_coverage.status is not CoverageStatus.ended
        for c in stubs.changes
    )


@pytest.mark.parametrize(
    ("after", "expected"),
    [
        (INELIGIBLE, DenialReason.income_over_limit),
        (CANNOT_DETERMINE, DenialReason.missing_information),
    ],
)
async def test_FR_007_12_approve_denial_ends_coverage_with_reason_code(
    after,
    expected: DenialReason,  # type: ignore[no-untyped-def]
) -> None:
    stubs = RenewalStubs(result=ELIGIBLE, items=OUT_OF_DATE, after_response=after)
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env, stubs)
        notice_id = await reach_awaiting_applicant(handle, stubs)
        await send_response(handle, response(notice_id))
        await wait_for_state(handle, CaseState.under_review)
        await signal(handle, action(await _open_task_id(handle), A.approve_denial))
        final = await handle.result()

    assert final.outcome is CaseOutcome.ended_by_caseworker
    assert final.denial_reason is expected
    change = _decided(stubs)
    assert change.coverage is None  # nothing is renewed
    assert change.prior_coverage is not None
    assert change.prior_coverage.status is CoverageStatus.ended
    assert change.notice is not None and change.notice.notice_type is NoticeType.coverage_ended
    assert change.action is not None and change.action.action is A.approve_denial


async def test_FR_007_12_override_to_approve_renews_after_response() -> None:
    stubs = RenewalStubs(result=ELIGIBLE, items=OUT_OF_DATE, after_response=INELIGIBLE)
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env, stubs)
        notice_id = await reach_awaiting_applicant(handle, stubs)
        await send_response(handle, response(notice_id))
        await wait_for_state(handle, CaseState.under_review)
        task_id = await _open_task_id(handle)
        await signal(handle, action(task_id, A.override_to_approve, reason=SYNTH_REASON))
        final = await handle.result()

    assert final.outcome is CaseOutcome.renewed_after_response
    change = _decided(stubs)
    assert change.coverage is not None and change.coverage.renews_coverage_period_id == PERIOD_ID
    assert change.prior_coverage is not None
    assert change.prior_coverage.status is CoverageStatus.renewed
    assert change.event.reason_code == A.override_to_approve.value
    assert change.case_update is not None and change.case_update.override is not None


async def test_FR_007_14_coverage_end_passes_sets_continued_pending_review_and_escalates() -> None:
    stubs = RenewalStubs(result=ELIGIBLE, items=OUT_OF_DATE)
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env, stubs)
        held = await wait_for_state(handle, CaseState.under_review)
        assert held.open_review_task is not None
        await env.sleep(timedelta(days=46))  # past 2026-04-30
        for _ in range(100):
            if stubs.flags:
                break
            await asyncio.sleep(0.05)
        after = await status_of(handle)
        await handle.terminate("test done")

    [continued] = [c for c in stubs.changes if c.event.kind is CaseEventKind.coverage_continued]
    assert continued.prior_coverage is not None
    assert continued.prior_coverage.status is CoverageStatus.continued_pending_review
    assert [f.flag.value for f in stubs.flags] == ["escalated"]
    assert stubs.flags[0].task_id == held.open_review_task.task_id
    assert after.state is CaseState.under_review and after.outcome is None
    assert {t.value for t in TimerName} == {
        "response_deadline",
        "review_reminder",
        "review_escalation",
        "coverage_end",
        "redetermination_response_deadline",
    }  # the coverage end uses its existing name (ADR-007-04)


async def test_FR_007_14_coverage_end_during_response_wait_continues_coverage() -> None:
    stubs = RenewalStubs(result=ELIGIBLE, items=OUT_OF_DATE, period_end=date(2026, 4, 5))
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env, stubs)
        await reach_awaiting_applicant(handle, stubs)
        await env.sleep(timedelta(days=25))  # the period ended on day 19, the deadline is day 30
        for _ in range(100):
            if any(c.event.kind is CaseEventKind.coverage_continued for c in stubs.changes):
                break
            await asyncio.sleep(0.05)
        waiting = await status_of(handle)
        assert stubs.flags == []  # no task is open to escalate
        await env.sleep(timedelta(days=10))
        review = await wait_for_state(handle, CaseState.under_review)
        await handle.terminate("test done")

    assert waiting.state is CaseState.awaiting_applicant
    [continued] = [c for c in stubs.changes if c.event.kind is CaseEventKind.coverage_continued]
    assert continued.prior_coverage is not None
    assert continued.prior_coverage.status is CoverageStatus.continued_pending_review
    assert review.outcome is None
    opened = [c.review_task for c in stubs.changes if c.review_task is not None][-1]
    assert opened is not None and opened.flags and opened.flags[0].value == "escalated"


async def test_FR_007_03_prepare_request_retries_exhausted_leaves_case_open_not_locked() -> None:
    stubs = RenewalStubs(result=ELIGIBLE, items=OUT_OF_DATE, prepare_failures=3)
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env, stubs)
        held = await wait_for_state(handle, CaseState.under_review)
        description = await handle.describe()
        await handle.terminate("test done")

    assert stubs.prepare_attempts == 4  # retried until it worked, never failed the case
    assert held.open_review_task is not None
    assert description.status is not None and description.status.name == "RUNNING"
    assert all(c.case_update is None or c.case_update.open for c in stubs.changes)


async def test_FR_007_13_reference_lookup_outage_retries_and_case_not_failed() -> None:
    stubs = RenewalStubs(result=ELIGIBLE, check_failures=3)
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env, stubs)
        final = await handle.result()

    assert stubs.check_attempts == 4
    assert final.outcome is CaseOutcome.renewed_automatically


async def test_FR_007_02_redetermination_case_start_runs_renewal_path() -> None:
    stubs = RenewalStubs(result=ELIGIBLE)
    async with environment() as env, worker(env.client, stubs):
        handle, data = await start(env, stubs)
        await handle.result()

    definition = workflow._Definition.must_from_class(CaseWorkflow)
    assert "applicant_response" in definition.signals
    assert "caseworker_action" in definition.signals
    assert [c.case_id for c in stubs.checks] == [data.case_id]


@settings(max_examples=6, deadline=None)
@given(
    after=st.sampled_from([INELIGIBLE, CANNOT_DETERMINE]),
    approve=st.booleans(),
    sleep_days=st.integers(min_value=0, max_value=60),
)
def test_FR_007_04_coverage_never_ends_without_caseworker(
    after,
    approve: bool,
    sleep_days: int,  # type: ignore[no-untyped-def]
) -> None:
    async def run() -> RenewalStubs:
        stubs = RenewalStubs(result=ELIGIBLE, items=OUT_OF_DATE, after_response=after)
        async with environment() as env, worker(env.client, stubs):
            handle, _ = await start(env, stubs)
            notice_id = await reach_awaiting_applicant(handle, stubs)
            await send_response(handle, response(notice_id))
            await wait_for_state(handle, CaseState.under_review)
            await env.sleep(timedelta(days=sleep_days))
            if approve:
                await signal(handle, action(await _open_task_id(handle), A.approve_denial))
                await handle.result()
            else:
                await handle.terminate("test done")
        return stubs

    stubs = asyncio.run(run())
    for change in stubs.changes:
        if change.prior_coverage and change.prior_coverage.status is CoverageStatus.ended:
            assert change.action is not None and change.action.action is A.approve_denial
    assert (
        any(
            c.prior_coverage and c.prior_coverage.status is CoverageStatus.ended
            for c in stubs.changes
        )
        is approve
    )


async def test_FR_007_03_history_and_notices_hold_labels_not_personal_data() -> None:
    stubs = RenewalStubs(result=ELIGIBLE, items=OUT_OF_DATE)
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env, stubs)
        await reach_awaiting_applicant(handle, stubs)
        await handle.terminate("test done")
        history = await handle.fetch_history()

    payloads = history.to_json()
    for needle in ("given_name", "family_name", "date_of_birth", "monthly_gross", "employer"):
        assert needle not in payloads
    assert all(
        set(item.model_dump()) <= {"kind", "member_id", "job_id", "reference_job_index"}
        for draft in stubs.drafts
        for item in draft.items
    )


async def test_FR_007_06_renewal_check_failure_paths_are_audited_when_refused() -> None:
    stubs = RenewalStubs(result=ELIGIBLE, items=OUT_OF_DATE)
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env, stubs)
        held = await wait_for_state(handle, CaseState.under_review)
        task_id = held.open_review_task.task_id  # type: ignore[union-attr]
        await signal(handle, action(task_id, A.approve_denial))  # not allowed for a notice review
        for _ in range(100):
            if any(c.event.kind is CaseEventKind.action_rejected for c in stubs.changes):
                break
            await asyncio.sleep(0.05)
        await reach_awaiting_applicant(handle, stubs)
        before = len(stubs.changes)
        await signal(handle, action(task_id, A.approve))  # the task is already closed
        for _ in range(100):
            if len(stubs.changes) > before:
                break
            await asyncio.sleep(0.05)
        await handle.terminate("test done")

    rejected = [c for c in stubs.changes if c.event.kind is CaseEventKind.action_rejected]
    assert len(rejected) >= 2
    assert all(c.action is not None and c.action.outcome.value == "rejected" for c in rejected)
    assert rejected[-1].event.reason_code == RejectionCode.task_closed.value
