from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from db.seeds.ids import HOUSEHOLD_A, HOUSEHOLD_B
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
    assert ids == {HOUSEHOLD_A, HOUSEHOLD_B}
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
