"""Thin review activities over fake repositories (TW-9, TW-11, PL-11)."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest
from structlog.testing import capture_logs
from temporalio.testing import ActivityEnvironment

from apps.worker.activities.review_tasks import ReviewTaskActivities
from apps.worker.review_payloads import ReviewFlagUpdate, ReviewTaskClose
from packages.domain.review import (
    ActionOutcome,
    ActionType,
    CaseworkerAction,
    RejectionCode,
    ReviewFlag,
    ReviewTask,
)
from tests.apps.worker.review_support import REASON_TEXT, T0, recorded_action, stored_task


class FakeReviewTasks:
    def __init__(self) -> None:
        self.documents: dict[UUID, ReviewTask] = {}

    async def upsert(self, task: ReviewTask) -> None:
        self.documents.setdefault(task.task_id, task)

    async def set_flag(
        self, household_id: UUID, case_id: UUID, task_id: UUID, flag: ReviewFlag, at_case: object
    ) -> bool:
        return task_id in self.documents

    async def close(
        self, household_id: UUID, case_id: UUID, task_id: UUID, action_id: UUID
    ) -> bool:
        return task_id in self.documents


class FakeCaseworkerActions:
    def __init__(self) -> None:
        self.documents: dict[UUID, CaseworkerAction] = {}

    async def record(self, action: CaseworkerAction) -> CaseworkerAction:
        return self.documents.setdefault(action.action_id, action)


def environment() -> ActivityEnvironment:
    env = ActivityEnvironment()
    env.info = replace(
        env.info, workflow_id="case-review", start_to_close_timeout=timedelta(seconds=30)
    )
    return env


@pytest.mark.parametrize(
    ("outcome", "code"),
    [(ActionOutcome.applied, None), (ActionOutcome.rejected, RejectionCode.reason_required)],
)
async def test_FR_010_05_free_text_reason_never_logged(
    outcome: ActionOutcome, code: RejectionCode | None
) -> None:
    task = stored_task()
    action = recorded_action(
        task, ActionType.override_to_approve, outcome=outcome, rejection_code=code
    )
    activities = ReviewTaskActivities(FakeReviewTasks(), FakeCaseworkerActions())
    env = environment()

    with capture_logs() as events:
        await env.run(activities.upsert_review_task, task)
        await env.run(activities.record_caseworker_action, action)
        await env.run(
            activities.set_review_flag,
            ReviewFlagUpdate(
                household_id=task.household_id,
                case_id=task.case_id,
                task_id=task.task_id,
                flag=ReviewFlag.reminder,
                at_case=T0,
            ),
        )
        await env.run(
            activities.close_review_task,
            ReviewTaskClose(
                household_id=task.household_id,
                case_id=task.case_id,
                task_id=task.task_id,
                action_id=action.action_id,
            ),
        )

    assert events, "the activities must log something for this check to mean anything"
    assert all(e.get("case_id") == str(task.case_id) for e in events)
    assert all(e.get("workflow_id") == "case-review" for e in events)
    assert REASON_TEXT not in repr(events)
    assert "income.monthly" not in repr(events)


async def test_FR_010_01_upsert_review_task_activity_is_idempotent() -> None:
    tasks = FakeReviewTasks()
    activities = ReviewTaskActivities(tasks, FakeCaseworkerActions())
    task = stored_task()
    env = environment()

    await env.run(activities.upsert_review_task, task)
    await env.run(activities.upsert_review_task, task)

    assert list(tasks.documents.values()) == [task]
