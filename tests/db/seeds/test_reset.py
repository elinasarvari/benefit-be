from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from db.seeds.ids import HOUSEHOLD_A, HOUSEHOLD_B, SEED
from db.seeds.reset import ResetRefused, guard, reset
from packages.platform.settings import Settings
from tests.integration.fixtures import open_client, rebuild


async def test_FR_000_02_reset_restores_seeded_counts_and_markers(
    integration_settings: Settings, owner_url: str
) -> None:
    async with open_client(owner_url) as client:
        db = client[integration_settings.MONGO_DB]
        await rebuild(integration_settings)
        await db["households"].insert_one({"_id": uuid.uuid4(), "created_at": datetime.now(UTC)})
        await db["households"].delete_one({"_id": HOUSEHOLD_A})

        await reset(integration_settings, analytics=False)

        ids = {doc["_id"] async for doc in db["households"].find({}, {"_id": 1})}
        marker = await db["_meta"].find_one({"_id": "dataset"})
    assert len(ids) == 200
    assert {HOUSEHOLD_A, HOUSEHOLD_B} <= ids
    assert marker is not None
    assert marker["synthetic"] is True


async def test_FR_000_02_reset_refuses_non_local_host(integration_settings: Settings) -> None:
    remote = integration_settings.model_copy(
        update={
            "MONGO_URL_OWNER": integration_settings.MONGO_URL_OWNER.__class__(
                "mongodb://owner:pw@db.example.com:27017/?replicaSet=rs0"
            )
        }
    )

    with pytest.raises(ResetRefused, match="non-local"):
        await guard(remote)


async def test_FR_000_02_reset_refuses_unmarked_non_empty_database(
    integration_settings: Settings, owner_url: str
) -> None:
    try:
        async with open_client(owner_url) as client:
            await client[integration_settings.MONGO_DB]["_meta"].delete_many({})

        with pytest.raises(ResetRefused, match="no synthetic marker"):
            await guard(integration_settings)
    finally:
        await rebuild(integration_settings)


async def test_FR_003_10_meta_records_synthetic_seed_and_anchor_date(
    integration_settings: Settings, owner_url: str
) -> None:
    await reset(integration_settings, analytics=False)

    async with open_client(owner_url) as client:
        meta = await client[integration_settings.MONGO_DB]["_meta"].find_one({"_id": "dataset"})
    assert meta is not None
    assert meta["synthetic"] is True
    assert meta["seed"] == SEED
    assert meta["anchor_date"].date() == integration_settings.SEED_ANCHOR_DATE


async def test_FR_003_06_reset_mirrors_both_rule_sets_into_collection(
    integration_settings: Settings, owner_url: str
) -> None:
    await reset(integration_settings, analytics=False)

    async with open_client(owner_url) as client:
        docs = [d async for d in client[integration_settings.MONGO_DB]["rule_sets"].find({})]
    assert {d["_id"] for d in docs} >= {"IL-ADULT-2025@1", "IL-ADULT-2026@1"}
