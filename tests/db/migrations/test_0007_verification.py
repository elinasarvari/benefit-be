"""Migration 0007 (verification validators and indexes, feature 002; FR-002-22, KD-11)."""

from __future__ import annotations

import importlib
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from pymongo.errors import DuplicateKeyError, WriteError

from db.client import Client
from db.migrate import MigrationContext, down, up
from packages.platform.settings import Settings
from tests.integration.fixtures import open_client

verification = importlib.import_module("db.migrations.versions.0007_verification")
NOW = datetime(2026, 11, 10, tzinfo=UTC)


def _ctx(client: Client, settings: Settings) -> MigrationContext:
    return MigrationContext(client, client[settings.MONGO_DB], client[settings.MONGO_AUDIT_DB])


async def _schema(ctx: MigrationContext, name: str) -> dict[str, Any]:
    async for info in await ctx.db.list_collections(filter={"name": name}):
        schema: dict[str, Any] = info["options"]["validator"]["$jsonSchema"]
        return schema
    raise AssertionError(name)


async def test_FR_002_22_migration_0007_applies_and_rolls_back(
    integration_settings: Settings, owner_url: str
) -> None:
    async with open_client(owner_url) as client:
        ctx = _ctx(client, integration_settings)
        try:
            await down(ctx, "0006_mvp")
            for name in verification.SCHEMAS:
                before = await _schema(ctx, name)
                assert "scenario_tags" not in before["properties"]
                assert "checked_at" not in before["properties"]
                assert "doc_type" not in before["properties"]
                info = await ctx.db[name].index_information()
                assert not {i for n, _k, i, _u in verification.INDEXES if n == name} & set(info)

            await up(ctx)
            for name, schema in verification.SCHEMAS.items():
                assert await _schema(ctx, name) == schema
            for name, _keys, index_name, unique in verification.INDEXES:
                info = await ctx.db[name].index_information()
                assert info[index_name].get("unique", False) is unique, (name, index_name)
            # 0006's own indexes are still there
            pay = await ctx.db["pay_stub_details"].index_information()
            assert pay["document_unique"]["unique"] is True
        finally:
            await up(ctx)


async def test_FR_002_22_migration_0007_validators_reject_bad_documents(
    integration_settings: Settings, owner_url: str
) -> None:
    household, member = uuid.uuid4(), uuid.uuid4()
    good = {
        "_id": uuid.uuid4(),
        "household_id": household,
        "member_id": member,
        "given_name": "Ana",
        "family_name": "Rivera",
        "date_of_birth": NOW,
        "jobs": [{"employer": "Acme (synthetic)", "monthly_gross": "1300.00"}],
        "scenario_tags": [],
        "synthetic": True,
    }
    async with open_client(owner_url) as client:
        ctx = _ctx(client, integration_settings)
        records = ctx.db["reference_records"]
        try:
            with pytest.raises(WriteError):  # money must be a string
                await records.insert_one(
                    {**good, "jobs": [{"employer": "Acme", "monthly_gross": 1300.0}]}
                )
            with pytest.raises(WriteError):  # the birth date is a BSON date
                await records.insert_one({**good, "date_of_birth": "1990-01-01"})
            with pytest.raises(WriteError):  # synthetic only
                await records.insert_one({**good, "synthetic": False})
            without_marker = {k: v for k, v in good.items() if k != "synthetic"}
            with pytest.raises(WriteError):  # the marker is required (PR-3)
                await records.insert_one(without_marker)
            with pytest.raises(WriteError):  # objects are closed
                await records.insert_one({**good, "unexpected": 1})
            await records.insert_one(good)
            with pytest.raises(DuplicateKeyError):
                await records.insert_one({**good, "_id": uuid.uuid4()})

            case = uuid.uuid4()
            results = ctx.db["verification_results"]
            with pytest.raises(WriteError):  # a finding needs a known cause
                await results.insert_one(
                    {
                        "_id": case,
                        "case_id": case,
                        "household_id": household,
                        "complete": True,
                        "needs_review": False,
                        "findings": [{"finding_id": uuid.uuid4(), "cause": "eligible"}],
                        "missing_items": [],
                        "checked_at": NOW,
                        "synthetic": True,
                    }
                )
            documents = ctx.db["documents"]
            with pytest.raises(WriteError):  # only pay stubs
                await documents.insert_one(
                    {
                        "_id": uuid.uuid4(),
                        "case_id": case,
                        "household_id": household,
                        "doc_type": "passport",
                        "created_at": NOW,
                    }
                )
        finally:
            await records.delete_many({"household_id": household})
