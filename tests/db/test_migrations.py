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
    assert sorted(recorded) == [
        "0001_baseline",
        "0002_review_queue",
        "0003_case_lifecycle",
        "0004_case_screens",
        "0005_eligibility",
        "0006_mvp",
        "0007_decision_explanations",
    ]


# --- 0003_case_lifecycle (001, KD-11) -------------------------------------------------------

CASE_COLLECTIONS = [
    "cases",
    "case_events",
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


async def test_FR_001_01_migration_0003_downgrade_restores_baseline_validators(
    integration_settings: Settings, owner_url: str
) -> None:
    async with open_client(owner_url) as client:
        ctx = _ctx(client, integration_settings)
        tightened = await _validators(ctx.db)

        downgraded = await down(ctx, "0002_review_queue")
        baseline = await _validators(ctx.db)
        baseline_indexes = await ctx.db["cases"].index_information()
        loose_insert = await ctx.db["cases"].insert_one(
            {"_id": uuid.uuid4(), "household_id": uuid.uuid4(), "case_id": uuid.uuid4()}
        )
        await ctx.db["cases"].delete_one({"_id": loose_insert.inserted_id})

        upgraded = await up(ctx)
        again = await _validators(ctx.db)
        indexes = await ctx.db["cases"].index_information()

    assert downgraded == [
        "0007_decision_explanations",
        "0006_mvp",
        "0005_eligibility",
        "0004_case_screens",
        "0003_case_lifecycle",
    ]
    assert upgraded == [
        "0003_case_lifecycle",
        "0004_case_screens",
        "0005_eligibility",
        "0006_mvp",
        "0007_decision_explanations",
    ]
    for name in CASE_COLLECTIONS:
        assert baseline[name] != tightened[name]
        assert again[name] == tightened[name]
    assert baseline["households"] == {"$jsonSchema": HOUSEHOLDS_SCHEMA}
    assert "submission_reference_unique" not in baseline_indexes
    assert "household_open_unique" not in baseline_indexes
    assert {"submission_reference_unique", "household_open_unique", "state_deadline"} <= set(
        indexes
    )


# --- 0004_case_screens (001, KD-11, ADR-001-08) ----------------------------------------------


def _application(case_id: uuid.UUID | None = None) -> dict[str, Any]:
    case_id = case_id or uuid.uuid4()
    now = datetime(2026, 3, 17, 15, tzinfo=UTC)
    return {
        "_id": case_id,
        "case_id": case_id,
        "household_id": uuid.uuid4(),
        "submission_reference": f"sub-{uuid.uuid4()}",
        "form": {
            "members": [
                {
                    "full_name": "SYNTH-NAME",
                    "date_of_birth": datetime(1990, 1, 1, tzinfo=UTC),
                    "relationship": "self",
                    "citizenship_status": "us_citizen",
                    "medicare_entitled": False,
                    "zero_income": True,
                    "jobs": [],
                }
            ],
            "address": {
                "line1": "1 Synthetic Way",
                "city": "Springfield",
                "state": "IL",
                "postal_code": "62701",
            },
        },
        "submitted_at": now,
        "synthetic": True,
        "created_at": now,
        "updated_at": now,
    }


def _with_member_field(doc: dict[str, Any]) -> dict[str, Any]:
    doc["form"]["members"][0]["unexpected"] = "x"
    return doc


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: {**d, "unexpected_field": "x"},
        _with_member_field,
        lambda d: {**d, "synthetic": False},
        lambda d: {k: v for k, v in d.items() if k != "synthetic"},
    ],
    ids=["top-level-extra", "nested-member-extra", "synthetic-false", "synthetic-missing"],
)
async def test_FR_001_24_migration_0004_applications_validator_is_strict(
    integration_settings: Settings, owner_url: str, mutate: Any
) -> None:
    async with open_client(owner_url) as client:
        applications = client[integration_settings.MONGO_DB]["applications"]

        accepted = await applications.insert_one(_application())
        with pytest.raises(WriteError):
            await applications.insert_one(mutate(_application()))

    assert accepted.acknowledged


async def test_FR_001_27_migration_0004_adds_open_deadline_index_and_downgrade_drops_both(
    integration_settings: Settings, owner_url: str
) -> None:
    async with open_client(owner_url) as client:
        ctx = _ctx(client, integration_settings)
        indexes = await ctx.db["cases"].index_information()
        collections = set(await ctx.db.list_collection_names())

        downgraded = await down(ctx, "0003_case_lifecycle")
        down_indexes = await ctx.db["cases"].index_information()
        down_collections = set(await ctx.db.list_collection_names())

        upgraded = await up(ctx)
        again_indexes = await ctx.db["cases"].index_information()
        again_collections = set(await ctx.db.list_collection_names())

    key = [("open", 1), ("decision_deadline", 1), ("_id", 1)]
    assert indexes["open_deadline"]["key"] == key
    assert "applications" in collections
    assert downgraded == [
        "0007_decision_explanations",
        "0006_mvp",
        "0005_eligibility",
        "0004_case_screens",
    ]
    assert "open_deadline" not in down_indexes
    assert "applications" not in down_collections
    assert "submission_reference_unique" in down_indexes  # 0003 is untouched
    assert upgraded == [
        "0004_case_screens",
        "0005_eligibility",
        "0006_mvp",
        "0007_decision_explanations",
    ]
    assert again_indexes["open_deadline"]["key"] == key
    assert "applications" in again_collections
