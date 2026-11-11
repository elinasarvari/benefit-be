"""Migration 0006 (MVP contracts, specs/mvp-contracts.md section 5)."""

from __future__ import annotations

import importlib
import uuid

import pytest
from pymongo.errors import DuplicateKeyError, WriteError

from db.client import Client
from db.migrate import MigrationContext, down, up
from packages.platform.settings import Settings
from tests.integration.fixtures import open_client

mvp = importlib.import_module("db.migrations.versions.0006_mvp")
CREATED = [*mvp.CREATED_WITH_CASE, *mvp.CREATED_HOUSEHOLD_ONLY]


def _ctx(client: Client, settings: Settings) -> MigrationContext:
    return MigrationContext(client, client[settings.MONGO_DB], client[settings.MONGO_AUDIT_DB])


async def test_mvp_migration_0006_creates_collections_and_downgrade_drops_them(
    integration_settings: Settings, owner_url: str
) -> None:
    async with open_client(owner_url) as client:
        ctx = _ctx(client, integration_settings)
        try:
            await down(ctx, "0005_eligibility")
            assert not set(CREATED) & set(await ctx.db.list_collection_names())

            await up(ctx)
            names = set(await ctx.db.list_collection_names())
            assert set(CREATED) <= names
            for name, _keys, index_name, unique in mvp.INDEXES:
                info = await ctx.db[name].index_information()
                assert info[index_name].get("unique", False) is unique, (name, index_name)
        finally:
            await up(ctx)


async def test_mvp_migration_0006_validators_require_household_and_case(
    integration_settings: Settings, owner_url: str
) -> None:
    async with open_client(owner_url) as client:
        ctx = _ctx(client, integration_settings)
        notices = ctx.db["notices"]
        with pytest.raises(WriteError):
            await notices.insert_one({"_id": uuid.uuid4(), "household_id": uuid.uuid4()})
        household, member = uuid.uuid4(), uuid.uuid4()
        records = ctx.db["reference_records"]
        try:
            await records.insert_one(
                {"_id": uuid.uuid4(), "household_id": household, "member_id": member}
            )
            with pytest.raises(DuplicateKeyError):
                await records.insert_one(
                    {"_id": uuid.uuid4(), "household_id": household, "member_id": member}
                )
        finally:
            await records.delete_many({"household_id": household})
