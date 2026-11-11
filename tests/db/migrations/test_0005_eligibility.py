from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pymongo.errors import WriteError

from db.migrate import MigrationContext, down, up
from db.seeds.records import to_document
from packages.domain.eligibility import DeterminationRecord
from packages.platform.settings import Settings
from packages.rules import evaluate
from tests.integration.fixtures import open_client
from tests.packages.rules.conftest import AS_OF, eligible_snapshot, uid


def _record() -> DeterminationRecord:
    return DeterminationRecord(
        determination_id=uid("determination"),
        household_id=uid("household"),
        case_id=uid("case"),
        snapshot_id=uid("snapshot"),
        recorded_at=datetime(2026, 6, 1, tzinfo=UTC),
        determination=evaluate(eligible_snapshot(), AS_OF),
    )


async def test_FR_003_06_migration_0005_upgrade_enforces_validators_and_downgrade_restores_baseline(
    integration_settings: Settings, owner_url: str
) -> None:
    valid = to_document(_record(), "determination_id")
    invalid = to_document(_record(), "determination_id")
    invalid["_id"] = uid("determination-2")
    invalid["determination"]["result"] = "maybe"
    async with open_client(owner_url) as client:
        ctx = MigrationContext(
            client,
            client[integration_settings.MONGO_DB],
            client[integration_settings.MONGO_AUDIT_DB],
        )
        determinations = ctx.db["determinations"]
        await determinations.delete_many({})
        try:
            await determinations.insert_one(valid)
            with pytest.raises(WriteError):
                await determinations.insert_one(invalid)
            await down(ctx, "0001_baseline")
            await determinations.delete_many({})
            await determinations.insert_one(invalid)  # the baseline validator accepts it
        finally:
            await determinations.delete_many({})
            await up(ctx)
