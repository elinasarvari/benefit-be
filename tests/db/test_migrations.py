from __future__ import annotations

from db.migrate import MIGRATIONS_COLLECTION, MigrationContext, down, up
from packages.platform.settings import Settings
from tests.integration.fixtures import open_client

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
    assert sorted(recorded) == ["0001_baseline", "0002_review_queue"]
