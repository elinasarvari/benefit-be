from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pymongo.errors import OperationFailure, WriteError

from packages.domain.audit import (
    ActorType,
    AuditAction,
    AuditLogEntry,
    AuditOutcome,
)
from packages.platform.audit import MongoAuditWriter
from packages.platform.settings import Settings
from tests.integration.fixtures import open_client

UNAUTHORIZED = 13


def entry() -> AuditLogEntry:
    return AuditLogEntry(
        occurred_at=datetime.now(UTC),
        actor_type=ActorType.user,
        actor_id="user-1",
        action=AuditAction.sign_in,
        outcome=AuditOutcome.success,
        reference_id="ref-1",
    )


async def test_FR_000_23_app_user_can_insert_and_read_audit_entries(
    integration_settings: Settings, app_url: str
) -> None:
    async with open_client(app_url) as client:
        await MongoAuditWriter(client, integration_settings.MONGO_AUDIT_DB).write(entry())

        found = await client[integration_settings.MONGO_AUDIT_DB]["audit_log"].find_one(
            {"reference_id": "ref-1"}
        )

    assert found is not None
    assert found["action"] == "sign_in"


@pytest.mark.parametrize(
    "operation",
    [
        lambda c: c.update_one({}, {"$set": {"outcome": "failure"}}),
        lambda c: c.update_many({}, {"$set": {"outcome": "failure"}}),
        lambda c: c.replace_one({}, {"x": 1}),
        lambda c: c.delete_one({}),
        lambda c: c.delete_many({}),
        lambda c: c.drop(),
        lambda c: c.rename("audit_log_renamed"),
    ],
    ids=["update_one", "update_many", "replace_one", "delete_one", "delete_many", "drop", "rename"],
)
async def test_FR_000_23_app_user_cannot_change_or_remove_audit_entries(
    integration_settings: Settings, app_url: str, operation: Any
) -> None:
    async with open_client(app_url) as client:
        collection = client[integration_settings.MONGO_AUDIT_DB]["audit_log"]
        await MongoAuditWriter(client, integration_settings.MONGO_AUDIT_DB).write(entry())

        with pytest.raises(OperationFailure) as failure:
            await operation(collection)

    assert failure.value.code == UNAUTHORIZED


async def test_FR_000_23_app_user_cannot_alter_the_validator_with_collmod(
    integration_settings: Settings, app_url: str
) -> None:
    async with open_client(app_url) as client:
        db = client[integration_settings.MONGO_AUDIT_DB]

        with pytest.raises(OperationFailure) as failure:
            await db.command("collMod", "audit_log", validator={})

    assert failure.value.code == UNAUTHORIZED


@pytest.mark.parametrize(
    "extra",
    [
        {"free_text": "any note"},
        {"nested": {"name": "Jane"}},
        {"reference_id": "x" * 1000},
        {"action": "not_an_action"},
    ],
    ids=["extra_field", "nested_object", "long_string", "unknown_action"],
)
async def test_FR_000_23_audit_schema_has_no_free_text_column(
    integration_settings: Settings, app_url: str, extra: dict[str, Any]
) -> None:
    document = {
        "occurred_at": datetime.now(UTC),
        "actor_type": "user",
        "action": "sign_in",
        "outcome": "success",
        "reference_id": "ref-2",
        **extra,
    }
    async with open_client(app_url) as client:
        with pytest.raises(WriteError):
            await client[integration_settings.MONGO_AUDIT_DB]["audit_log"].insert_one(document)


async def test_FR_000_23_app_user_cannot_read_other_collections_in_the_audit_database(
    integration_settings: Settings, app_url: str
) -> None:
    async with open_client(app_url) as client:
        with pytest.raises(OperationFailure) as failure:
            await client[integration_settings.MONGO_AUDIT_DB]["anything_else"].insert_one({"x": 1})

    assert failure.value.code == UNAUTHORIZED
