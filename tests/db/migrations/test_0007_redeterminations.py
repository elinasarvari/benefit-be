"""Migration 0007 (007 redeterminations, ADR-007-03, ADR-007-04)."""

from __future__ import annotations

import importlib
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from pymongo.errors import WriteError

from db.client import Client
from db.migrate import MigrationContext, down, up
from packages.platform.settings import Settings
from tests.integration.fixtures import open_client

redetermination = importlib.import_module("db.migrations.versions.0007_redeterminations")


def _ctx(client: Client, settings: Settings) -> MigrationContext:
    return MigrationContext(client, client[settings.MONGO_DB], client[settings.MONGO_AUDIT_DB])


async def _properties(db: Any, name: str) -> dict[str, Any]:
    schema = await redetermination._schema(db, name)
    properties: dict[str, Any] = schema["properties"]
    return properties


def _case_event(kind: str) -> dict[str, Any]:
    now = datetime(2026, 11, 10, tzinfo=UTC)
    return {
        "_id": uuid.uuid4(),
        "case_id": uuid.uuid4(),
        "household_id": uuid.uuid4(),
        "seq": 1,
        "kind": kind,
        "case_time": now,
        "real_time": now,
        "actor_type": "system",
        "reason_code": "coverage_continued",
    }


async def test_FR_007_05_migration_0007_up_down_validator_accepts_new_fields(
    integration_settings: Settings, owner_url: str
) -> None:
    async with open_client(owner_url) as client:
        ctx = _ctx(client, integration_settings)
        try:
            await down(ctx, "0006_mvp")
            assert "coverage_period_id" not in await _properties(ctx.db, "cases")
            assert "renews_coverage_period_id" not in await _properties(ctx.db, "coverage_periods")
            with pytest.raises(WriteError):
                await ctx.db["case_events"].insert_one(_case_event("coverage_continued"))

            await up(ctx)
            assert "coverage_period_id" in await _properties(ctx.db, "cases")
            assert "renews_coverage_period_id" in await _properties(ctx.db, "coverage_periods")
            events = await _properties(ctx.db, "case_events")
            assert "coverage_continued" in events["kind"]["enum"]
            audit = await _properties(ctx.audit_db, "audit_log")
            assert "redetermination_started_by_schedule" in audit["action"]["enum"]
            assert "synthetic" in events or "synthetic" in await _properties(ctx.db, "cases")
            inserted = await ctx.db["case_events"].insert_one(_case_event("coverage_continued"))
            await ctx.db["case_events"].delete_one({"_id": inserted.inserted_id})
        finally:
            await up(ctx)
