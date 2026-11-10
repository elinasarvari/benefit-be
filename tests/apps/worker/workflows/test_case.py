"""CaseWorkflow time-skipping tests, activities stubbed (TW-13, TW-15; test-plan TC-30–TC-45)."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest

from apps.worker.workflows.case import CaseWorkflow
from apps.worker.workflows.case_states import coverage_for
from packages.domain.case import (
    ActionOutcome,
    CaseChange,
    CaseEventKind,
    CaseOutcome,
    CaseState,
    CaseworkerActionType,
    DenialReason,
    DeterminationOutcome,
    EventActorType,
    NoticeType,
    ReviewReason,
    ReviewTaskStatus,
)
from tests.apps.worker.workflows.case_harness import (
    CANNOT_DETERMINE,
    DETERMINATION_ID,
    ELIGIBLE,
    INELIGIBLE,
    RECEIVED_ON,
    REVIEWER,
    SYNTH_REASON,
    Stubs,
    action,
    determination,
    environment,
    signal,
    start,
    status_of,
    wait_for_changes,
    wait_for_state,
    worker,
)

A = CaseworkerActionType


def deciding(stubs: Stubs) -> CaseChange:
    [change] = [c for c in stubs.changes if c.event.to_state is CaseState.decided]
    return change


async def test_FR_001_16_eligible_decides_approved() -> None:
    stubs = Stubs(result=ELIGIBLE)
    async with environment() as env, worker(env.client, stubs):
        handle, data = await start(env)
        final = await handle.result()

    assert final.state is CaseState.decided
    assert final.outcome is CaseOutcome.approved
    change = deciding(stubs)
    assert change.case_update is not None and change.case_update.open is False
    assert change.coverage is not None and change.notice is not None  # one change, one transaction
    assert (change.coverage.start_date, change.coverage.end_date) == coverage_for(RECEIVED_ON)
    assert change.notice.notice_type is NoticeType.approval
    assert change.notice.status == "requested"
    assert stubs.determine_calls[0].as_of_date == RECEIVED_ON


async def test_FR_001_09_ineligible_waits_for_approve_denial() -> None:
    stubs = Stubs(result=INELIGIBLE)
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env)
        waiting = await wait_for_state(handle, CaseState.under_review)
        assert waiting.open_review_task is not None
        assert waiting.open_review_task.reason is ReviewReason.adverse_determination

        await signal(handle, action(waiting.open_review_task.task_id, A.approve_denial))
        final = await handle.result()

    assert final.outcome is CaseOutcome.denied
    assert final.denial_reason is DenialReason.income_over_limit
    change = deciding(stubs)
    assert change.notice is not None and change.notice.notice_type is NoticeType.denial
    assert change.review_task is not None
    assert change.review_task.status is ReviewTaskStatus.resolved
    assert change.event.actor_type is EventActorType.user and change.event.actor_id == REVIEWER
    assert change.coverage is None


async def test_FR_001_16_cannot_determine_opens_task_never_decides() -> None:
    stubs = Stubs(result=CANNOT_DETERMINE)
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env)
        waiting = await wait_for_state(handle, CaseState.under_review)
        await env.sleep(timedelta(days=50))
        still = await status_of(handle)
        await handle.terminate("test done")

    assert still.state is CaseState.under_review
    assert still.outcome is None
    assert waiting.open_review_task is not None
    assert waiting.open_review_task.reason is ReviewReason.cannot_determine
    [opening] = [c for c in stubs.changes if c.review_task is not None]
    assert opening.review_task is not None
    assert opening.review_task.evidence.missing_items == ["pay_stub"]
    assert not any(c.event.to_state is CaseState.decided for c in stubs.changes)


async def test_FR_001_20_override_keeps_determination() -> None:
    stubs = Stubs(result=INELIGIBLE)
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env)
        waiting = await wait_for_state(handle, CaseState.under_review)
        assert waiting.open_review_task is not None
        task_id = waiting.open_review_task.task_id

        await signal(handle, action(task_id, A.override_to_approve, reason=SYNTH_REASON))
        final = await handle.result()

    assert final.outcome is CaseOutcome.approved
    change = deciding(stubs)
    assert change.case_update is not None and change.case_update.override is not None
    assert change.case_update.override.reviewer_id == REVIEWER
    assert change.case_update.override.reason == SYNTH_REASON
    assert change.case_update.override.determination_id == DETERMINATION_ID
    assert change.coverage is not None
    assert change.notice is not None and change.notice.notice_type is NoticeType.approval
    # The determination is only read: no change carries a new determination outcome.
    assert all(c.event.reason_code != DeterminationOutcome.eligible.value for c in stubs.changes)


OUTAGE_ATTEMPTS = 12  # more than the 5 attempts of the DEFAULT retry policy


async def test_FR_001_03_record_case_change_outage_beyond_retry_policy_still_decides() -> None:
    stubs = Stubs(result=ELIGIBLE, record_failures=OUTAGE_ATTEMPTS)
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env)
        final = await handle.result()

    assert stubs.record_attempts > OUTAGE_ATTEMPTS
    assert final.state is CaseState.decided
    assert final.outcome is CaseOutcome.approved
    assert [c.seq for c in stubs.changes] == [1, 2, 3]  # each change written once, in order
    assert deciding(stubs).case_update is not None
    assert deciding(stubs).case_update.open is False  # the household is not locked out


async def test_FR_001_03_determination_outage_beyond_retry_policy_still_decides() -> None:
    stubs = Stubs(result=ELIGIBLE, determine_failures=OUTAGE_ATTEMPTS)
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env)
        final = await handle.result()

    assert stubs.determine_attempts == OUTAGE_ATTEMPTS + 1
    assert len(stubs.determine_calls) == 1
    assert final.outcome is CaseOutcome.approved


async def test_FR_001_02_disallowed_action_recorded_rejected() -> None:
    """TC-34: an action signalled on a decided case is refused and recorded; nothing moves.

    The signal lands while the deciding change is being written, so the workflow sees it only
    once the case is decided (a completed workflow can't take signals; the API records those,
    ADR-001-04)."""
    decided_seen = asyncio.Event()
    release = asyncio.Event()

    async def hold_decision(change: CaseChange) -> None:
        if change.event.to_state is CaseState.decided:
            decided_seen.set()
            await release.wait()

    stubs = Stubs(result=INELIGIBLE, on_change=hold_decision)
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env)
        waiting = await wait_for_state(handle, CaseState.under_review)
        assert waiting.open_review_task is not None
        task_id = waiting.open_review_task.task_id

        await signal(handle, action(task_id, A.approve_denial))
        await asyncio.wait_for(decided_seen.wait(), timeout=10)
        await signal(handle, action(task_id, A.request_information))  # not in any allowed list
        release.set()
        final = await handle.result()

    decided_at = stubs.changes.index(deciding(stubs))
    rejected = [c for c in stubs.changes if c.event.kind is CaseEventKind.action_rejected]
    assert len(rejected) == 1
    assert stubs.changes.index(rejected[0]) > decided_at  # refused after the decision
    assert rejected[0].event.reason_code == "no_open_task"
    assert rejected[0].event.actor_id == REVIEWER
    assert rejected[0].action is not None
    assert rejected[0].action.action is A.request_information
    assert rejected[0].action.outcome is ActionOutcome.rejected
    assert rejected[0].case_update is None and rejected[0].review_task is None  # nothing moved
    assert final.state is CaseState.decided
    assert final.outcome is CaseOutcome.denied


async def test_FR_001_02_disallowed_action_on_open_task_recorded_rejected() -> None:
    stubs = Stubs(result=INELIGIBLE)
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env)
        waiting = await wait_for_state(handle, CaseState.under_review)
        assert waiting.open_review_task is not None
        task_id = waiting.open_review_task.task_id

        await signal(handle, action(task_id, A.request_information))  # not allowed in 001
        await wait_for_changes(stubs, 4)
        await signal(handle, action(task_id, A.approve_denial))
        final = await handle.result()

    rejected = [c for c in stubs.changes if c.event.kind is CaseEventKind.action_rejected]
    assert len(rejected) == 1
    assert rejected[0].event.reason_code == "action_not_allowed"
    assert rejected[0].action is not None
    assert rejected[0].action.outcome is ActionOutcome.rejected
    assert rejected[0].case_update is None  # the case did not move
    assert final.outcome is CaseOutcome.denied


async def test_FR_001_02_action_on_wrong_task_recorded_rejected() -> None:
    import uuid

    stubs = Stubs(result=INELIGIBLE)
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env)
        waiting = await wait_for_state(handle, CaseState.under_review)
        assert waiting.open_review_task is not None

        await signal(handle, action(uuid.uuid4(), A.approve_denial))
        await wait_for_changes(stubs, 4)
        still = await status_of(handle)
        await handle.terminate("test done")

    assert stubs.changes[-1].event.kind is CaseEventKind.action_rejected
    assert still.state is CaseState.under_review


async def test_FR_001_04_duplicate_request_id_ignored() -> None:
    import uuid

    stubs = Stubs(result=INELIGIBLE)
    request_id = uuid.uuid4()
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env)
        waiting = await wait_for_state(handle, CaseState.under_review)
        assert waiting.open_review_task is not None
        task_id = waiting.open_review_task.task_id

        reject = action(task_id, A.request_information, request_id=request_id)
        await signal(handle, reject)
        await signal(handle, reject)
        await wait_for_changes(stubs, 4)
        await signal(handle, action(task_id, A.approve_denial))
        await handle.result()

    actions = [c.action.id for c in stubs.changes if c.action is not None]
    assert actions.count(request_id) == 1
    assert len(stubs.changes) == 5  # verifying, determining, under_review, rejected, decided


async def test_FR_001_13_events_carry_case_and_real_time() -> None:
    stubs = Stubs(result=INELIGIBLE)
    async with environment() as env, worker(env.client, stubs):
        handle, data = await start(env, scale=1440)
        waiting = await wait_for_state(handle, CaseState.under_review)
        assert waiting.open_review_task is not None
        await env.sleep(timedelta(hours=1))  # one real hour = 60 case days
        await signal(handle, action(waiting.open_review_task.task_id, A.approve_denial))
        await handle.result()

    clock = data.clock
    for change in stubs.changes:
        event = change.event
        assert event.case_time == clock.case_start + (event.real_time - clock.real_start) * 1440
    first, last = stubs.changes[0].event, stubs.changes[-1].event
    assert last.case_time - first.case_time >= timedelta(days=60)


async def test_FR_001_16_income_and_other_rule_fail_denies_income_over_limit() -> None:
    # Both rules fail, the other rule listed first; the workflow still names income
    # (case_states.denial_reason_for).
    stubs = Stubs(
        result=determination(
            DeterminationOutcome.ineligible,
            DenialReason.other_rule_not_met,
            failed_rules=[DenialReason.other_rule_not_met, DenialReason.income_over_limit],
        )
    )
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env)
        waiting = await wait_for_state(handle, CaseState.under_review)
        assert waiting.open_review_task is not None
        await signal(handle, action(waiting.open_review_task.task_id, A.approve_denial))
        final = await handle.result()

    assert final.denial_reason is DenialReason.income_over_limit
    opened = [c.review_task for c in stubs.changes if c.review_task is not None]
    assert opened[0].evidence.failed_rules == ["other_rule_not_met", "income_over_limit"]
    change = deciding(stubs)
    assert change.case_update is not None
    assert change.case_update.denial_reason is DenialReason.income_over_limit


async def test_FR_001_07_review_wait_holds_no_pending_activity() -> None:
    stubs = Stubs(result=INELIGIBLE)
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env)
        await wait_for_state(handle, CaseState.under_review)
        await wait_for_changes(stubs, 3)
        await asyncio.sleep(0.2)
        description = await handle.describe()
        await handle.terminate("test done")

    assert list(description.raw_description.pending_activities) == []


async def test_FR_001_09_ineligible_stays_under_review_past_deadline_without_action() -> None:
    stubs = Stubs(result=INELIGIBLE)
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env)
        await wait_for_state(handle, CaseState.under_review)
        await env.sleep(timedelta(days=60))
        still = await status_of(handle)
        await handle.terminate("test done")

    assert still.state is CaseState.under_review
    assert still.outcome is None
    assert still.days_remaining is not None and still.days_remaining < 0
    assert still.at_risk is True


async def test_FR_001_20_override_without_reason_recorded_rejected() -> None:
    stubs = Stubs(result=INELIGIBLE)
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env)
        waiting = await wait_for_state(handle, CaseState.under_review)
        assert waiting.open_review_task is not None

        await signal(handle, action(waiting.open_review_task.task_id, A.override_to_approve))
        await wait_for_changes(stubs, 4)
        still = await status_of(handle)
        await handle.terminate("test done")

    last = stubs.changes[-1]
    assert last.event.kind is CaseEventKind.action_rejected
    assert last.event.reason_code == "reason_required"
    assert last.action is not None and last.action.outcome is ActionOutcome.rejected
    assert still.state is CaseState.under_review


async def test_FR_001_07_allowed_actions_query_lists_open_task_actions() -> None:
    stubs = Stubs(result=INELIGIBLE)
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env)
        waiting = await wait_for_state(handle, CaseState.under_review)
        assert waiting.open_review_task is not None
        open_actions = await handle.query(CaseWorkflow.allowed_actions)

        await signal(handle, action(waiting.open_review_task.task_id, A.approve_denial))
        await handle.result()
        after = await handle.query(CaseWorkflow.allowed_actions)

    assert open_actions.task_id == waiting.open_review_task.task_id
    assert open_actions.actions == [A.approve_denial, A.override_to_approve]
    assert after.task_id is None and after.actions == []


async def test_FR_001_02_happy_path_visits_submitted_verifying_determining_decided() -> None:
    stubs = Stubs(result=ELIGIBLE)
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env)
        await handle.result()

    assert stubs.kinds_and_states() == [
        ("state_changed", "submitted", "verifying"),
        ("state_changed", "verifying", "determining"),
        ("state_changed", "determining", "decided"),
    ]
    assert stubs.verify_calls == 1


@pytest.mark.parametrize(
    ("result", "final_action"),
    [(ELIGIBLE, None), (INELIGIBLE, A.approve_denial), (CANNOT_DETERMINE, A.override_to_approve)],
)
async def test_FR_001_10_every_state_change_recorded_with_actor_and_reason(
    result: object, final_action: CaseworkerActionType | None
) -> None:
    stubs = Stubs(result=result)  # type: ignore[arg-type]
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env)
        if final_action is not None:
            waiting = await wait_for_state(handle, CaseState.under_review)
            assert waiting.open_review_task is not None
            reason = SYNTH_REASON if final_action is A.override_to_approve else None
            await signal(handle, action(waiting.open_review_task.task_id, final_action, reason))
        await handle.result()

    assert [c.seq for c in stubs.changes] == list(range(1, len(stubs.changes) + 1))
    for change in stubs.changes:
        event = change.event
        assert event.reason_code
        if event.actor_type is EventActorType.user:
            assert event.actor_id == REVIEWER
        else:
            assert event.actor_id is None
    assert stubs.changes[-1].event.to_state is CaseState.decided


async def test_FR_001_11_day_36_shows_9_remaining_at_risk() -> None:
    stubs = Stubs(result=INELIGIBLE)
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env)
        await wait_for_state(handle, CaseState.under_review)
        await env.sleep(timedelta(days=36))
        status = await status_of(handle)
        await handle.terminate("test done")

    assert status.received_on == RECEIVED_ON
    assert status.decision_deadline == date(2026, 5, 1)
    assert status.days_remaining == 9
    assert status.at_risk is True


async def test_FR_001_11_decided_case_has_no_days_remaining() -> None:
    stubs = Stubs(result=ELIGIBLE)
    async with environment() as env, worker(env.client, stubs):
        handle, _ = await start(env)
        final = await handle.result()
        queried = await status_of(handle)

    for status in (final, queried):
        assert status.state is CaseState.decided
        assert status.days_remaining is None
        assert status.at_risk is False
