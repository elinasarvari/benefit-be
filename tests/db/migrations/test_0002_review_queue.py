"""Migration 0002_review_queue: validators and indexes for review tasks and caseworker actions.

TC-35 to TC-38 (feature 010). Synthetic UUIDs only (PR-3); each test cleans up its documents.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError, WriteError

from db.migrate import MigrationContext, down, up
from packages.domain.review import ReviewCause, ReviewReason
from packages.platform.settings import Settings
from tests.integration.fixtures import open_client

BASELINE = "0001_baseline"
VERSION = "0002_review_queue"
NOW = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)
BASELINE_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "household_id", "case_id"],
        "properties": {"household_id": {"bsonType": "binData"}, "case_id": {"bsonType": "binData"}},
    }
}
QUEUE_INDEXES = {"status_deadline", "status_flags_deadline"}
ACTION_INDEXES = {"action_id_unique", "task_recorded"}


@pytest.fixture
async def ctx(integration_settings: Settings, owner_url: str) -> AsyncIterator[MigrationContext]:
    async with open_client(owner_url) as client:
        yield MigrationContext(
            client,
            client[integration_settings.MONGO_DB],
            client[integration_settings.MONGO_AUDIT_DB],
        )


def _collection(db: AsyncDatabase, name: str) -> AsyncCollection:  # type: ignore[type-arg]
    return db[name]


def _task(household_id: UUID, case_id: UUID, **overrides: Any) -> dict[str, Any]:
    task_id = uuid4()
    doc: dict[str, Any] = {
        "_id": task_id,
        "task_id": task_id,
        "household_id": household_id,
        "case_id": case_id,
        "workflow_id": f"case-{case_id}",
        "reason": ReviewReason.adverse_determination.value,
        "cause": ReviewCause.ineligible.value,
        "evidence_refs": [{"kind": "determination", "id": uuid4()}],
        "blocking": True,
        "status": "open",
        "opened_at_case": NOW,
        "opened_at_real": NOW,
        "reminder_at": None,
        "escalated_at": None,
        "flags": [],
        "decision_deadline": datetime(2026, 3, 30, tzinfo=UTC),
        "coverage_end": None,
        "closed_by_action_id": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    doc.update(overrides)
    return doc


def _action(household_id: UUID, case_id: UUID, **overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "action_id": uuid4(),
        "task_id": uuid4(),
        "household_id": household_id,
        "case_id": case_id,
        "action": "approve_denial",
        "reviewer_id": uuid4(),
        "reviewer_role": "caseworker",
        "reason": None,
        "corrected_value": None,
        "finding_id": None,
        "revision_id": None,
        "submitted_at": NOW,
        "outcome": "applied",
        "rejection_code": None,
        "applied_at_case": NOW,
        "recorded_at": NOW,
    }
    doc.update(overrides)
    return doc


async def _validator(db: AsyncDatabase, name: str) -> dict[str, Any]:  # type: ignore[type-arg]
    async for info in await db.list_collections(filter={"name": name}):
        validator: dict[str, Any] = info["options"]["validator"]
        return validator
    raise AssertionError(f"collection {name} missing")


async def _index_names(db: AsyncDatabase, name: str) -> set[str]:  # type: ignore[type-arg]
    return {index["name"] async for index in await db[name].list_indexes()}


def _plan_indexes(plan: dict[str, Any]) -> tuple[set[str], set[str]]:
    """Index names and stage names used anywhere in a winning plan."""
    indexes, stages = set(), set()
    pending = [plan]
    while pending:
        node = pending.pop()
        stages.add(node.get("stage", ""))
        if "indexName" in node:
            indexes.add(node["indexName"])
        pending.extend(node.get("inputStages", []))
        if "inputStage" in node:
            pending.append(node["inputStage"])
        if "queryPlan" in node:
            pending.append(node["queryPlan"])
    return indexes, stages


async def test_FR_010_01_review_tasks_validator_rejects_unknown_reason_and_extra_fields(
    ctx: MigrationContext,
) -> None:
    tasks = _collection(ctx.db, "review_tasks")
    household_id, case_id = uuid4(), uuid4()
    valid = _task(household_id, case_id)
    try:
        await tasks.insert_one(valid)
        with pytest.raises(WriteError):
            await tasks.insert_one(_task(household_id, case_id, reason="supervisor_whim"))
        with pytest.raises(WriteError):
            await tasks.insert_one(_task(household_id, case_id, applicant_name="Synthetic Person"))
        with pytest.raises(WriteError):
            await tasks.insert_one(
                {k: v for k, v in _task(household_id, case_id).items() if k != "household_id"}
            )
        with pytest.raises(WriteError):
            await tasks.insert_one(
                _task(
                    household_id,
                    case_id,
                    evidence_refs=[{"kind": "document", "id": uuid4(), "text": "x"}],
                )
            )
        stored = await tasks.find_one({"_id": valid["_id"], "household_id": household_id})
    finally:
        await tasks.delete_many({"household_id": household_id})

    schema = (await _validator(ctx.db, "review_tasks"))["$jsonSchema"]
    assert stored is not None
    assert schema["additionalProperties"] is False
    assert {"household_id", "case_id"} <= set(schema["required"])
    assert set(schema["properties"]["reason"]["enum"]) == {r.value for r in ReviewReason}
    assert set(schema["properties"]["cause"]["enum"]) == {c.value for c in ReviewCause}


async def test_FR_010_06_caseworker_actions_unique_action_id_index(ctx: MigrationContext) -> None:
    actions = _collection(ctx.db, "caseworker_actions")
    household_id, case_id = uuid4(), uuid4()
    first = _action(household_id, case_id)
    try:
        await actions.insert_one(dict(first))
        with pytest.raises(DuplicateKeyError):
            await actions.insert_one({**first, "_id": uuid4(), "recorded_at": NOW})
        with pytest.raises(WriteError):
            await actions.insert_one(_action(household_id, case_id, outcome="maybe"))
        with pytest.raises(WriteError):
            await actions.insert_one(_action(household_id, case_id, reason=""))
        count = await actions.count_documents({"household_id": household_id})
    finally:
        await actions.delete_many({"household_id": household_id})

    assert count == 1


async def test_FR_010_01_migration_0002_down_restores_baseline(ctx: MigrationContext) -> None:
    try:
        reverted = await down(ctx, BASELINE)
        task_validator = await _validator(ctx.db, "review_tasks")
        action_validator = await _validator(ctx.db, "caseworker_actions")
        task_indexes = await _index_names(ctx.db, "review_tasks")
        action_indexes = await _index_names(ctx.db, "caseworker_actions")
    finally:
        reapplied = await up(ctx)

    assert reverted == [VERSION]
    assert task_validator == BASELINE_VALIDATOR
    assert action_validator == BASELINE_VALIDATOR
    assert not task_indexes & QUEUE_INDEXES
    assert not action_indexes & ACTION_INDEXES
    assert {"case_created", "household"} <= task_indexes
    assert reapplied == [VERSION]
    assert QUEUE_INDEXES <= await _index_names(ctx.db, "review_tasks")
    assert ACTION_INDEXES <= await _index_names(ctx.db, "caseworker_actions")


async def test_FR_010_02_queue_and_escalated_queries_use_indexes(ctx: MigrationContext) -> None:
    tasks = _collection(ctx.db, "review_tasks")
    household_id, case_id = uuid4(), uuid4()
    docs = [_task(household_id, case_id) for _ in range(40)]
    docs += [_task(household_id, case_id, flags=["reminder", "escalated"]) for _ in range(3)]
    docs += [_task(household_id, case_id, status="closed") for _ in range(20)]
    try:
        await tasks.insert_many(docs)
        queue = await tasks.find({"status": "open"}).sort("decision_deadline", 1).explain()
        escalated = (
            await tasks.find({"status": "open", "flags": "escalated"})
            .sort("decision_deadline", 1)
            .explain()
        )
    finally:
        await tasks.delete_many({"household_id": household_id})

    queue_indexes, queue_stages = _plan_indexes(queue["queryPlanner"]["winningPlan"])
    escalated_indexes, escalated_stages = _plan_indexes(escalated["queryPlanner"]["winningPlan"])
    assert queue_indexes == {"status_deadline"}
    assert escalated_indexes == {"status_flags_deadline"}
    assert not {"COLLSCAN", "SORT"} & (queue_stages | escalated_stages)
