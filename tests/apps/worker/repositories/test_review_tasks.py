"""`review_tasks` projection writes against the local MongoDB (R4, constitution VIII, FR-010-02)."""

from __future__ import annotations

import uuid

import pytest

from apps.worker.repositories.review_tasks import ReviewTaskRepository
from packages.domain.review import ReviewFlag, ReviewTaskStatus
from packages.platform.clock import FixedClock
from packages.platform.mongo import Client
from tests.apps.worker.review_support import T0, stored_task

pytestmark = pytest.mark.integration


def repository(client: Client, db_name: str) -> ReviewTaskRepository:
    return ReviewTaskRepository(client[db_name], FixedClock(T0))


async def test_FR_010_01_repository_writes_scoped_to_household_and_case(
    app_client: Client, db_name: str
) -> None:
    repo = repository(app_client, db_name)
    mine, theirs = stored_task(), stored_task()
    await repo.upsert(mine)
    await repo.upsert(theirs)

    # The right task ID with another household's scope must not touch it.
    closed_wrong_scope = await repo.close(
        mine.household_id, mine.case_id, theirs.task_id, uuid.uuid4()
    )
    flagged_wrong_scope = await repo.set_flag(
        theirs.household_id, mine.case_id, theirs.task_id, ReviewFlag.reminder, T0
    )
    seen_wrong_scope = await repo.get(mine.household_id, mine.case_id, theirs.task_id)
    untouched = await repo.get(theirs.household_id, theirs.case_id, theirs.task_id)

    assert (closed_wrong_scope, flagged_wrong_scope) == (False, False)
    assert seen_wrong_scope is None
    assert untouched is not None
    assert untouched.status is ReviewTaskStatus.open
    assert untouched.flags == []


async def test_FR_010_02_set_flag_and_close_ignored_on_closed_task(
    app_client: Client, db_name: str
) -> None:
    repo = repository(app_client, db_name)
    task = stored_task()
    action_id = uuid.uuid4()
    await repo.upsert(task)
    assert await repo.close(task.household_id, task.case_id, task.task_id, action_id)

    flagged = await repo.set_flag(
        task.household_id, task.case_id, task.task_id, ReviewFlag.reminder, T0
    )
    closed_again = await repo.close(task.household_id, task.case_id, task.task_id, uuid.uuid4())
    await repo.upsert(task)  # a retried upsert must not reopen it either
    stored = await repo.get(task.household_id, task.case_id, task.task_id)

    assert (flagged, closed_again) == (False, False)
    assert stored is not None
    assert stored.status is ReviewTaskStatus.closed
    assert stored.closed_by_action_id == action_id
    assert stored.flags == []
    assert stored.decision_deadline == task.decision_deadline
