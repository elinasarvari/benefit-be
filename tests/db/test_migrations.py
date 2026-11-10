from __future__ import annotations

import importlib
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError, WriteError

from db.client import Client
from db.migrate import MIGRATIONS_COLLECTION, MigrationContext, down, up
from packages.platform.settings import Settings
from tests.integration.fixtures import open_client

HOUSEHOLDS_SCHEMA = importlib.import_module(
    "db.migrations.versions.0001_baseline"
).HOUSEHOLDS_SCHEMA

EXPECTED_COLLECTIONS = {"users", "households", "_meta", "cases", "documents", "tool_calls"}


async def test_baseline_upgrade_downgrade_upgrade_round_trip(
    integration_settings: Settings, owner_url: str
) -> None:
    async with open_client(owner_url) as client:
        ctx = MigrationContext(
            client,
            client[integration_settings.MONGO_DB],
            client[integration_settings.MONGO_AUDIT_DB],
        )

        await down(ctx)
        left = set(await ctx.db.list_collection_names()) - {MIGRATIONS_COLLECTION}
        audit_left = await ctx.audit_db.list_collection_names()
        await up(ctx)
        present = set(await ctx.db.list_collection_names())
        audit_present = await ctx.audit_db.list_collection_names()

    assert left == set()
    assert audit_left == []
    assert EXPECTED_COLLECTIONS <= present
    assert audit_present == ["audit_log"]


async def test_migration_runner_records_and_skips_applied_versions(
    integration_settings: Settings, owner_url: str
) -> None:
    async with open_client(owner_url) as client:
        ctx = MigrationContext(
            client,
            client[integration_settings.MONGO_DB],
            client[integration_settings.MONGO_AUDIT_DB],
        )

        again = await up(ctx)
        recorded = [doc["_id"] async for doc in ctx.db[MIGRATIONS_COLLECTION].find()]

    assert again == []
    assert recorded == ["0001_baseline", "0002_case_lifecycle"]


# --- 0002_case_lifecycle (001, KD-11) -------------------------------------------------------

CASE_COLLECTIONS = [
    "cases",
    "case_events",
    "review_tasks",
    "caseworker_actions",
    "coverage_periods",
    "outbox",
]


def _ctx(client: Client, settings: Settings) -> MigrationContext:
    return MigrationContext(client, client[settings.MONGO_DB], client[settings.MONGO_AUDIT_DB])


def _case(household_id: uuid.UUID, reference: str, *, state: str = "submitted") -> dict[str, Any]:
    case_id = uuid.uuid4()
    now = datetime(2026, 3, 17, 15, tzinfo=UTC)
    decided = state == "decided"
    return {
        "_id": case_id,
        "case_id": case_id,
        "household_id": household_id,
        "case_type": "application",
        "submission_reference": reference,
        "state": state,
        "open": not decided,
        "received_on": datetime(2026, 3, 17, tzinfo=UTC),
        "decision_deadline": datetime(2026, 5, 1, tzinfo=UTC),
        "clock": {"real_start": now, "case_start": now, "scale": 1, "offset_seconds": 0},
        "outcome": "approved" if decided else None,
        "denial_reason": None,
        "override": None,
        "decided_on": datetime(2026, 3, 18, tzinfo=UTC) if decided else None,
        "last_seq": 0,
        "created_at": now,
        "updated_at": now,
    }


def _event(case_id: uuid.UUID, seq: int, *, reason_code: str = "started") -> dict[str, Any]:
    now = datetime(2026, 3, 17, 15, tzinfo=UTC)
    return {
        "_id": uuid.uuid4(),
        "case_id": case_id,
        "household_id": uuid.uuid4(),
        "seq": seq,
        "kind": "state_changed",
        "from_state": "submitted",
        "to_state": "verifying",
        "case_time": now,
        "real_time": now,
        "actor_type": "system",
        "actor_id": None,
        "reason_code": reason_code,
        "review_task_id": None,
        "created_at": now,
    }


async def _validators(db: AsyncDatabase[dict[str, Any]]) -> dict[str, Any]:
    return {
        info["name"]: info["options"].get("validator") async for info in await db.list_collections()
    }


async def test_FR_001_23_partial_unique_index_allows_one_open_case_per_household(
    integration_settings: Settings, owner_url: str
) -> None:
    household = uuid.uuid4()
    async with open_client(owner_url) as client:
        cases = client[integration_settings.MONGO_DB]["cases"]
        await cases.insert_one(_case(household, f"sub-{uuid.uuid4()}"))

        with pytest.raises(DuplicateKeyError):
            await cases.insert_one(_case(household, f"sub-{uuid.uuid4()}"))

        other = uuid.uuid4()
        await cases.insert_one(_case(other, f"sub-{uuid.uuid4()}", state="decided"))
        await cases.insert_one(_case(other, f"sub-{uuid.uuid4()}"))
        open_count = await cases.count_documents({"household_id": other, "open": True})

    assert open_count == 1


async def test_FR_001_01_submission_reference_is_unique(
    integration_settings: Settings, owner_url: str
) -> None:
    reference = f"sub-A-{uuid.uuid4()}"
    async with open_client(owner_url) as client:
        cases = client[integration_settings.MONGO_DB]["cases"]
        await cases.insert_one(_case(uuid.uuid4(), reference, state="decided"))

        with pytest.raises(DuplicateKeyError):
            await cases.insert_one(_case(uuid.uuid4(), reference, state="decided"))


async def test_FR_001_04_case_events_case_id_seq_is_unique(
    integration_settings: Settings, owner_url: str
) -> None:
    case_id = uuid.uuid4()
    async with open_client(owner_url) as client:
        events = client[integration_settings.MONGO_DB]["case_events"]
        await events.insert_one(_event(case_id, 1))

        with pytest.raises(DuplicateKeyError):
            await events.insert_one(_event(case_id, 1))
        await events.insert_one(_event(case_id, 2))


@pytest.mark.parametrize(
    ("collection", "document"),
    [
        ("cases", {"state": "closed"}),
        ("cases", {"state": "decided", "open": False, "outcome": "maybe"}),
        ("case_events", {"reason_code": "Caseworker said the income looked wrong"}),
        ("case_events", {"kind": "free_text"}),
        ("cases", {"unexpected_field": "x"}),
    ],
)
async def test_FR_001_02_validator_rejects_unknown_case_state_and_outcome(
    integration_settings: Settings,
    owner_url: str,
    collection: str,
    document: dict[str, Any],
) -> None:
    base = (
        _case(uuid.uuid4(), f"sub-{uuid.uuid4()}")
        if collection == "cases"
        else _event(uuid.uuid4(), 1)
    )
    async with open_client(owner_url) as client:
        target = client[integration_settings.MONGO_DB][collection]

        with pytest.raises(WriteError):
            await target.insert_one({**base, **document})


async def test_FR_001_01_migration_0002_downgrade_restores_baseline_validators(
    integration_settings: Settings, owner_url: str
) -> None:
    async with open_client(owner_url) as client:
        ctx = _ctx(client, integration_settings)
        tightened = await _validators(ctx.db)

        downgraded = await down(ctx, "0001_baseline")
        baseline = await _validators(ctx.db)
        baseline_indexes = await ctx.db["cases"].index_information()
        loose_insert = await ctx.db["cases"].insert_one(
            {"_id": uuid.uuid4(), "household_id": uuid.uuid4(), "case_id": uuid.uuid4()}
        )
        await ctx.db["cases"].delete_one({"_id": loose_insert.inserted_id})

        upgraded = await up(ctx)
        again = await _validators(ctx.db)
        indexes = await ctx.db["cases"].index_information()

    assert downgraded == ["0002_case_lifecycle"]
    assert upgraded == ["0002_case_lifecycle"]
    for name in CASE_COLLECTIONS:
        assert baseline[name] != tightened[name]
        assert again[name] == tightened[name]
    assert baseline["households"] == {"$jsonSchema": HOUSEHOLDS_SCHEMA}
    assert "submission_reference_unique" not in baseline_indexes
    assert "household_open_unique" not in baseline_indexes
    assert {"submission_reference_unique", "household_open_unique", "state_deadline"} <= set(
        indexes
    )
