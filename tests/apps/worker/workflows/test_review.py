"""Review tasks in the case workflow: opening, actions and results (FR-010-01, -04, -05, -06).

Runs against ReviewHarnessWorkflow, which composes `review.py` until 001's CaseWorkflow exists,
with every activity stubbed (TW-15) on the time-skipping test server (TW-13).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date
from typing import Any
from uuid import UUID

import pytest
from temporalio.client import WorkflowExecutionStatus, WorkflowHandle

from apps.worker.workflows.review_harness import (
    HarnessScenario,
    ReviewHarnessWorkflow,
)
from apps.worker.workflows.review_ports import (
    CaseStep,
    EligibilityOutcome,
    RequestOrigin,
    VerificationFinding,
)
from packages.domain.review import (
    ALLOWED_ACTIONS,
    ActionOutcome,
    ActionType,
    CaseworkerActionSignal,
    RejectionCode,
    ReviewCause,
    ReviewReason,
    ReviewTaskState,
)
from tests.apps.worker.review_support import REASON_TEXT, action_signal
from tests.apps.worker.workflows.review_env import (
    Stubs,
    harness_input,
    review_env,
    start,
    wait_for_open_tasks,
)

A = ActionType
FINDING_ID = uuid.UUID("00000000-0000-4000-8000-0000000000f1")

# FR-010-01 "Actions and result", written out again so the test does not trust the code's table.
EXPECTED_STEP: dict[tuple[ReviewReason, ActionType], CaseStep] = {
    (ReviewReason.adverse_determination, A.approve_denial): CaseStep.decided_denied,
    (ReviewReason.adverse_determination, A.override_to_approve): CaseStep.decided_approved,
    (ReviewReason.adverse_determination, A.request_information): CaseStep.request_drafted,
    (ReviewReason.cannot_determine, A.request_information): CaseStep.request_drafted,
    (ReviewReason.cannot_determine, A.correct_value): CaseStep.determining,
    (ReviewReason.notice_approval, A.approve): CaseStep.notice_sent,
    (ReviewReason.notice_approval, A.edit_and_approve): CaseStep.notice_sent,
    (ReviewReason.verification_review, A.accept_findings): CaseStep.determining,
    (ReviewReason.verification_review, A.correct_value): CaseStep.determining,
    (ReviewReason.verification_review, A.dismiss_finding): CaseStep.determining,
    (ReviewReason.verification_review, A.request_information): CaseStep.request_drafted,
    (ReviewReason.missed_response_deadline, A.deny_missing_information): CaseStep.decided_denied,
    (ReviewReason.missed_response_deadline, A.extend_15_days): CaseStep.awaiting_applicant,
    (ReviewReason.missed_response_deadline, A.continue_with_held_data): CaseStep.determining,
    (ReviewReason.explanation_review, A.approve): CaseStep.explanation_approved,
    (ReviewReason.explanation_review, A.edit_and_approve): CaseStep.explanation_approved,
}

# How the harness raises each reason, with the cause and evidence kind it must carry.
RAISE: dict[ReviewReason, tuple[HarnessScenario, ReviewCause, str]] = {
    ReviewReason.adverse_determination: (
        HarnessScenario.determine,
        ReviewCause.ineligible,
        "determination_id",
    ),
    ReviewReason.cannot_determine: (
        HarnessScenario.determine,
        ReviewCause.cannot_determine,
        "determination_id",
    ),
    ReviewReason.notice_approval: (
        HarnessScenario.notice_ready,
        ReviewCause.notice_ready,
        "notice_draft_id",
    ),
    ReviewReason.verification_review: (
        HarnessScenario.verify,
        ReviewCause.low_confidence,
        "finding_id",
    ),
    ReviewReason.missed_response_deadline: (
        HarnessScenario.missed_response_window,
        ReviewCause.response_deadline_missed,
        "request_id",
    ),
    ReviewReason.explanation_review: (
        HarnessScenario.explanation_check_failed,
        ReviewCause.explanation_check_failed,
        "explanation_id",
    ),
}


def stubs_for(reason: ReviewReason) -> Stubs:
    stubs = Stubs()
    if reason is ReviewReason.cannot_determine:
        stubs.evaluation = EligibilityOutcome.cannot_determine
    if reason is ReviewReason.verification_review:
        stubs.findings = [
            VerificationFinding(finding_id=FINDING_ID, cause=ReviewCause.low_confidence)
        ]
    return stubs


def signal_for(task: ReviewTaskState, action: ActionType, **kwargs: Any) -> CaseworkerActionSignal:
    extra: dict[str, UUID] = {}
    if action is A.dismiss_finding:
        extra["finding_id"] = FINDING_ID
    if action is A.edit_and_approve:
        extra["revision_id"] = uuid.uuid4()
    return action_signal(task.task_id, action, **{**extra, **kwargs})


async def wait_until(check: Any, message: str) -> None:
    for _ in range(200):
        if check():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(message)


async def progress(handle: WorkflowHandle[Any, Any]) -> list[str]:
    result: list[str] = await handle.query(ReviewHarnessWorkflow.progress, result_type=list[str])
    return result


@pytest.mark.parametrize("reason", list(ReviewReason))
async def test_FR_010_01_report_opens_task_with_reason_evidence_and_case_waits(
    reason: ReviewReason,
) -> None:
    scenario, cause, evidence_kind = RAISE[reason]
    stubs = stubs_for(reason)
    data = harness_input(scenario)

    async with review_env(stubs) as env:
        handle = await start(env, data)
        [task] = await wait_for_open_tasks(handle)
        await wait_until(lambda: stubs.upserts, "the task was never projected")
        steps = await progress(handle)
        status = (await handle.describe()).status

    assert (task.reason, task.cause) == (reason, cause)
    assert [ref.kind for ref in task.evidence_refs] == [evidence_kind]
    assert (task.household_id, task.case_id) == (data.household_id, data.case_id)
    assert task.blocking is (reason is not ReviewReason.explanation_review)
    assert task.decision_deadline == data.decision_deadline
    assert stubs.upserts[0].task_id == task.task_id
    assert status is WorkflowExecutionStatus.RUNNING
    if task.blocking:
        assert f"{scenario.value}:continued" not in steps


@pytest.mark.parametrize(("reason", "action"), sorted(EXPECTED_STEP))
async def test_FR_010_01_allowed_action_resumes_with_listed_result_and_records_reviewer(
    reason: ReviewReason, action: ActionType
) -> None:
    scenario, _, _ = RAISE[reason]
    stubs = stubs_for(reason)
    data = harness_input(scenario)

    async with review_env(stubs) as env:
        handle = await start(env, data)
        [task] = await wait_for_open_tasks(handle)
        signal = signal_for(task, action)
        await handle.signal(ReviewHarnessWorkflow.caseworker_action, signal)
        result = await handle.result()

    assert len(EXPECTED_STEP) == 16
    assert set(EXPECTED_STEP) == {(r, a) for r in ReviewReason for a in ALLOWED_ACTIONS[r]}
    [applied] = result.results
    assert applied.step is EXPECTED_STEP[(reason, action)]
    assert (applied.task_id, applied.action_id) == (task.task_id, signal.action_id)
    assert applied.reviewer_id == signal.reviewer_id
    [record] = stubs.records
    assert record.outcome is ActionOutcome.applied
    assert record.reviewer_id == signal.reviewer_id
    assert record.applied_at_case is not None
    assert record.reason == REASON_TEXT
    assert [(c.task_id, c.action_id) for c in stubs.closes] == [(task.task_id, signal.action_id)]
    # The action's own details reach the result, and only for that action.
    corrected = signal.corrected_value if action is A.correct_value else None
    assert applied.corrected_value == corrected
    assert applied.corrected_value is None or applied.corrected_value.value == 1200
    assert applied.dismissed_finding_id == (FINDING_ID if action is A.dismiss_finding else None)
    assert applied.revision_id == (signal.revision_id if action is A.edit_and_approve else None)
    has_draft = reason in {ReviewReason.notice_approval, ReviewReason.explanation_review}
    assert applied.draft_ref == (data.subject_id if has_draft else None)
    expected_request = {
        A.request_information: stubs.drafted_ids[-1] if stubs.drafted_ids else None,
        A.extend_15_days: data.subject_id,
    }.get(action)
    assert applied.request_id == expected_request
    if action is A.request_information:
        assert expected_request is not None
    assert applied.coverage_ended_by_caseworker is False


async def test_FR_010_01_deny_missing_information_on_redetermination_ends_coverage() -> None:
    stubs = Stubs()
    data = harness_input(HarnessScenario.missed_response_window, coverage_end=date(2026, 5, 31))

    async with review_env(stubs) as env:
        handle = await start(env, data)
        [task] = await wait_for_open_tasks(handle)
        signal = signal_for(task, A.deny_missing_information)
        await handle.signal(ReviewHarnessWorkflow.caseworker_action, signal)
        result = await handle.result()

    [applied] = result.results
    assert task.coverage_end == date(2026, 5, 31)
    assert applied.step is CaseStep.decided_denied
    assert applied.coverage_ended_by_caseworker is True


async def test_FR_010_05_disallowed_action_recorded_rejected_and_task_stays_open() -> None:
    stubs = stubs_for(ReviewReason.adverse_determination)

    async with review_env(stubs) as env:
        handle = await start(env, harness_input(HarnessScenario.determine))
        [task] = await wait_for_open_tasks(handle)
        await handle.signal(ReviewHarnessWorkflow.caseworker_action, signal_for(task, A.approve))
        await wait_until(lambda: stubs.records, "the rejected action was never recorded")
        still_open = await wait_for_open_tasks(handle)

    [record] = stubs.records
    assert record.outcome is ActionOutcome.rejected
    assert record.rejection_code is RejectionCode.not_allowed_for_reason
    assert record.applied_at_case is None
    assert [t.task_id for t in still_open] == [task.task_id]
    assert stubs.closes == []


async def test_FR_010_06_same_action_id_signalled_twice_applied_once() -> None:
    stubs = stubs_for(ReviewReason.adverse_determination)
    data = harness_input(HarnessScenario.determine).model_copy(update={"wait_for_finish": True})

    async with review_env(stubs) as env:
        handle = await start(env, data)
        [task] = await wait_for_open_tasks(handle)
        signal = signal_for(task, A.approve_denial)
        await handle.signal(ReviewHarnessWorkflow.caseworker_action, signal)
        await handle.signal(ReviewHarnessWorkflow.caseworker_action, signal)
        await wait_until(lambda: stubs.closes, "the action was never applied")
        await handle.signal(ReviewHarnessWorkflow.finish)
        result = await handle.result()

    assert len(result.results) == 1
    assert len(stubs.records) == 1
    assert len(stubs.closes) == 1


async def test_FR_010_01_override_to_approve_decides_approved_and_keeps_determination() -> None:
    stubs = stubs_for(ReviewReason.adverse_determination)

    async with review_env(stubs) as env:
        handle = await start(env, harness_input(HarnessScenario.determine))
        [task] = await wait_for_open_tasks(handle)
        signal = signal_for(task, A.override_to_approve)
        await handle.signal(ReviewHarnessWorkflow.caseworker_action, signal)
        result = await handle.result()

    [applied] = result.results
    assert applied.step is CaseStep.decided_approved
    assert applied.override_action_id == signal.action_id
    assert applied.reviewer_id == signal.reviewer_id
    assert result.determination is not None
    assert result.determination.determination_id == stubs.determination_id
    assert result.determination.outcome is EligibilityOutcome.ineligible
    assert stubs.records[0].reason == REASON_TEXT


async def test_FR_010_04_caseworker_request_information_drafted_after_auto_request_used() -> None:
    stubs = Stubs(
        findings=[
            VerificationFinding(
                finding_id=uuid.uuid4(),
                cause=ReviewCause.unreadable_document,
                missing_information=True,
            ),
            VerificationFinding(finding_id=FINDING_ID, cause=ReviewCause.low_confidence),
        ]
    )

    async with review_env(stubs) as env:
        handle = await start(env, harness_input(HarnessScenario.verify))
        [task] = await wait_for_open_tasks(handle)
        await handle.signal(
            ReviewHarnessWorkflow.caseworker_action, signal_for(task, A.request_information)
        )
        result = await handle.result()

    assert [d.origin for d in stubs.drafts] == [RequestOrigin.automatic, RequestOrigin.caseworker]
    assert result.auto_rfi_used is True
    assert result.results[0].step is CaseStep.request_drafted


@pytest.mark.parametrize(
    ("reason", "scenario"),
    [
        (ReviewReason.notice_approval, HarnessScenario.notice_ready),
        (ReviewReason.explanation_review, HarnessScenario.explanation_check_failed),
    ],
)
async def test_FR_010_01_edit_and_approve_keeps_draft_and_revision(
    reason: ReviewReason, scenario: HarnessScenario
) -> None:
    stubs = Stubs()
    data = harness_input(scenario)

    async with review_env(stubs) as env:
        handle = await start(env, data)
        [task] = await wait_for_open_tasks(handle)
        signal = signal_for(task, A.edit_and_approve)
        await handle.signal(ReviewHarnessWorkflow.caseworker_action, signal)
        result = await handle.result()

    [applied] = result.results
    assert applied.revision_id == signal.revision_id
    assert applied.draft_ref == data.subject_id
    assert stubs.records[0].revision_id == signal.revision_id
    assert stubs.upserts[0].evidence_refs[0].id == data.subject_id


async def test_FR_010_01_explanation_review_does_not_block_decision() -> None:
    stubs = Stubs()

    async with review_env(stubs) as env:
        handle = await start(env, harness_input(HarnessScenario.explanation_check_failed))
        [task] = await wait_for_open_tasks(handle)
        for _ in range(200):
            steps = await progress(handle)
            if "explanation_check_failed:continued" in steps:
                break
            await asyncio.sleep(0.05)
        still_open = await wait_for_open_tasks(handle)

    assert task.reason is ReviewReason.explanation_review
    assert task.blocking is False
    assert "explanation_check_failed:continued" in steps
    assert [t.task_id for t in still_open] == [task.task_id]
