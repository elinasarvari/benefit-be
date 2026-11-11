"""Migration 0005 (feature 003, ADR-003-03, ADR-003-04): TC-72, TC-73, TC-74."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from pymongo.errors import DuplicateKeyError, WriteError

from db.client import Client
from db.migrate import MigrationContext, down, up
from db.seeds.records import to_document
from packages.domain.eligibility import DeterminationRecord
from packages.platform.settings import Settings
from packages.rules import evaluate
from tests.integration.fixtures import open_client
from tests.packages.rules.conftest import AS_OF, eligible_snapshot, uid

STUB_COLLECTION = "household_stub_results"
VERIFICATIONS = "seeded_verifications"


def _ctx(client: Client, settings: Settings) -> MigrationContext:
    return MigrationContext(client, client[settings.MONGO_DB], client[settings.MONGO_AUDIT_DB])


def _record() -> DeterminationRecord:
    return DeterminationRecord(
        determination_id=uid("determination"),
        household_id=uid("household"),
        case_id=uid("case"),
        snapshot_id=uid("snapshot"),
        recorded_at=datetime(2026, 6, 1, tzinfo=UTC),
        determination=evaluate(eligible_snapshot(), AS_OF),
    )


def _snapshot_document(snapshot_id: str) -> dict[str, Any]:
    document = to_document(eligible_snapshot(), "snapshot_id")
    document["_id"] = uid(snapshot_id)
    return document


def _verification(member: str, household: str = "household") -> dict[str, Any]:
    return {
        "_id": uuid.uuid4(),
        "household_id": uid(household),
        "member_id": uid(member),
        "identity_verified": True,
        "jobs": [],
        "synthetic": True,
    }


async def test_FR_003_06_migration_0005_upgrade_enforces_validators_and_downgrade_restores_0004(
    integration_settings: Settings, owner_url: str
) -> None:
    """TC-72: ADR-003-04. At 0004 the stub store is back and the strict validators are gone."""
    valid = to_document(_record(), "determination_id")
    invalid = to_document(_record(), "determination_id")
    invalid["_id"] = uid("determination-2")
    invalid["determination"]["result"] = "maybe"
    async with open_client(owner_url) as client:
        ctx = _ctx(client, integration_settings)
        determinations = ctx.db["determinations"]
        try:
            await down(ctx, "0004_case_screens")
            names = set(await ctx.db.list_collection_names())
            assert STUB_COLLECTION in names
            assert VERIFICATIONS not in names
            await determinations.delete_many({})
            await determinations.insert_one(invalid)  # 0004's lenient validator accepts it
            await determinations.delete_many({})

            await up(ctx)
            names = set(await ctx.db.list_collection_names())
            assert STUB_COLLECTION not in names
            assert VERIFICATIONS in names
            await determinations.insert_one(valid)
            with pytest.raises(WriteError):
                await determinations.insert_one(invalid)
            with pytest.raises(WriteError):  # 001's stub field is gone from the validator
                await ctx.db["households"].insert_one(
                    {
                        "_id": uuid.uuid4(),
                        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
                        "address_state": "IL",
                        "seed_key": "probe",
                        "stub_result": "eligible",
                    }
                )
        finally:
            await determinations.delete_many({})
            await up(ctx)


async def test_FR_003_19_migration_0005_rejects_second_snapshot_for_same_case_and_date(
    integration_settings: Settings, owner_url: str
) -> None:
    """TC-73: one snapshot per `(case_id, as_of_date)` (ADR-003-03)."""
    async with open_client(owner_url) as client:
        snapshots = client[integration_settings.MONGO_DB]["household_snapshots"]
        await snapshots.delete_many({})
        try:
            await snapshots.insert_one(_snapshot_document("snapshot-1"))
            with pytest.raises(DuplicateKeyError):
                await snapshots.insert_one(_snapshot_document("snapshot-2"))
            without_date = _snapshot_document("snapshot-3")
            del without_date["as_of_date"]
            with pytest.raises(WriteError):
                await snapshots.insert_one(without_date)
        finally:
            await snapshots.delete_many({})


async def test_FR_003_16_migration_0005_creates_seeded_verifications_unique_per_member(
    integration_settings: Settings, owner_url: str
) -> None:
    """TC-74: household-only store, one document per `(household_id, member_id)`."""
    async with open_client(owner_url) as client:
        verifications = client[integration_settings.MONGO_DB][VERIFICATIONS]
        await verifications.delete_many({})
        try:
            await verifications.insert_one(_verification("self"))
            with pytest.raises(DuplicateKeyError):
                await verifications.insert_one(_verification("self"))
            await verifications.insert_one(_verification("spouse"))
            await verifications.insert_one(_verification("self", household="other-household"))
            invalid = _verification("child")
            invalid["identity_verified"] = "yes"
            with pytest.raises(WriteError):
                await verifications.insert_one(invalid)
        finally:
            await verifications.delete_many({})
