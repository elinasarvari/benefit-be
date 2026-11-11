"""`caseworker_actions` writes against the local MongoDB (FR-010-01, -05, -06, research R6, R8)."""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from pymongo.errors import DuplicateKeyError

from apps.worker.repositories.caseworker_actions import (
    EVAL_OVERRIDES,
    CaseworkerActionRepository,
)
from packages.domain.review import ActionOutcome, ActionType, RejectionCode
from packages.platform.audit import AUDIT_COLLECTION, MongoAuditWriter
from packages.platform.mongo import Client
from tests.apps.worker.review_support import recorded_action, stored_task

pytestmark = pytest.mark.integration


def repository(client: Client, db_name: str, audit_db_name: str) -> CaseworkerActionRepository:
    return CaseworkerActionRepository(client, db_name, MongoAuditWriter(client, audit_db_name))


async def count(client: Client, db: str, collection: str, action_id: UUID) -> int:
    return await client[db][collection].count_documents({"action_id": action_id})


async def test_FR_010_06_record_duplicate_action_id_keeps_one_document(
    app_client: Client, db_name: str, audit_db_name: str
) -> None:
    repo = repository(app_client, db_name, audit_db_name)
    action = recorded_action(stored_task())

    first = await repo.record(action)
    second = await repo.record(action)

    assert first == second == action
    assert await count(app_client, db_name, "caseworker_actions", action.action_id) == 1


@pytest.mark.parametrize("action_type", [ActionType.correct_value, ActionType.dismiss_finding])
async def test_FR_010_01_ai_override_writes_eval_override_in_same_transaction(
    app_client: Client, db_name: str, audit_db_name: str, action_type: ActionType
) -> None:
    repo = repository(app_client, db_name, audit_db_name)
    task = stored_task()
    applied = recorded_action(task, action_type, finding_id=uuid.uuid4())
    failing = recorded_action(task, action_type, finding_id=uuid.uuid4())
    # Force the second insert of `failing` to fail: its eval override already exists.
    await app_client[db_name][EVAL_OVERRIDES].insert_one(
        {"_id": failing.action_id, "finding_id": uuid.uuid4()}
    )

    await repo.record(applied)
    with pytest.raises(DuplicateKeyError):
        await repo.record(failing)

    assert await count(app_client, db_name, EVAL_OVERRIDES, applied.action_id) == 1
    assert await count(app_client, db_name, "caseworker_actions", applied.action_id) == 1
    assert await count(app_client, db_name, "caseworker_actions", failing.action_id) == 0


async def test_FR_010_05_rejected_action_writes_audit_refused(
    app_client: Client, db_name: str, audit_db_name: str
) -> None:
    repo = repository(app_client, db_name, audit_db_name)
    action = recorded_action(
        stored_task(),
        ActionType.approve,
        outcome=ActionOutcome.rejected,
        rejection_code=RejectionCode.not_allowed_for_reason,
    )

    await repo.record(action)
    entry = await app_client[audit_db_name][AUDIT_COLLECTION].find_one(
        {"reference_id": str(action.action_id)}
    )

    assert entry is not None
    assert (entry["action"], entry["outcome"]) == ("caseworker_action", "refused")
    assert (entry["target_type"], entry["target_id"]) == ("review_task", str(action.task_id))
    assert entry["actor_id"] == str(action.reviewer_id)
    assert await count(app_client, db_name, EVAL_OVERRIDES, action.action_id) == 0
