"""Baseline: the collections, validators and indexes for the platform (feature 000).

Every collection gets a `$jsonSchema` validator. The collections other features own start with a
minimal validator that those features tighten in their own migrations.
"""

from __future__ import annotations

from typing import Any

from pymongo.collation import Collation

from db.collections import AUDIT_COLLECTION
from db.migrate import MigrationContext

UUID = {"bsonType": "binData"}
ROLES = ["applicant", "caseworker", "supervisor"]
AUDIT_ACTIONS = [
    "sign_in",
    "access_refused",
    "caseworker_action",
    "demo_clock_action",
    "redetermination_started_on_demand",
    "metrics_refreshed",
]
REASON_CODES = [
    "invalid_credentials",
    "role_not_allowed",
    "wrong_household",
    "wrong_case",
    "credential_expired",
]

# Frozen: a later collection goes in a later migration, never into this list (KD-11).
SCOPED_WITH_CASE = [
    "cases",
    "case_events",
    "review_tasks",
    "caseworker_actions",
    "determinations",
    "household_snapshots",
    "outbox",
    "documents",
    "extractions",
    "agent_runs",
    "notice_drafts",
    "tool_calls",
]
SCOPED_HOUSEHOLD_ONLY = ["household_members", "coverage_periods"]
UNSCOPED = ["rule_sets", "policy_passages", "eval_overrides"]


def _string(max_length: int = 128) -> dict[str, Any]:
    return {"bsonType": "string", "maxLength": max_length}


USERS_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "additionalProperties": False,
    "required": ["_id", "username", "display_name", "password_hash", "role", "created_at"],
    "properties": {
        "_id": UUID,
        "username": {"bsonType": "string", "pattern": "^[a-z0-9_.-]{1,64}$"},
        "display_name": _string(),
        "password_hash": {"bsonType": "string", "pattern": "^\\$argon2id\\$"},
        "role": {"enum": ROLES},
        "household_id": {"bsonType": ["binData", "null"]},
        "created_at": {"bsonType": "date"},
    },
    # An applicant must have a household. Other roles must not.
    "anyOf": [
        {
            "required": ["household_id"],
            "properties": {"role": {"enum": ["applicant"]}, "household_id": UUID},
        },
        {
            "properties": {
                "role": {"enum": ["caseworker", "supervisor"]},
                "household_id": {"bsonType": "null"},
            }
        },
    ],
}

HOUSEHOLDS_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "additionalProperties": False,
    "required": ["_id", "created_at"],
    "properties": {"_id": UUID, "created_at": {"bsonType": "date"}},
}

AUDIT_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "additionalProperties": False,  # no free-text or nested field can be added
    "required": ["occurred_at", "actor_type", "action", "outcome", "reference_id"],
    "properties": {
        "_id": {"bsonType": "objectId"},
        "occurred_at": {"bsonType": "date"},
        "actor_type": {"enum": ["user", "system", "anonymous"]},
        "actor_id": _string(),
        "actor_role": {"enum": ROLES},
        "action": {"enum": AUDIT_ACTIONS},
        "target_type": _string(),
        "target_id": _string(),
        "outcome": {"enum": ["success", "failure", "refused"]},
        "reason_code": {"enum": REASON_CODES},
        "reference_id": _string(),
    },
}

META_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "additionalProperties": False,
    "required": ["_id", "synthetic", "seed", "generator_version", "generated_at"],
    "properties": {
        "_id": {"enum": ["dataset"]},
        "synthetic": {"enum": [True]},
        "seed": {"bsonType": ["int", "long"]},
        "generator_version": _string(),
        "generated_at": {"bsonType": "date"},
    },
}


def _minimal(required: list[str]) -> dict[str, Any]:
    properties: dict[str, Any] = {name: {} for name in required if name != "_id"}
    properties.update({"household_id": UUID, "case_id": UUID})
    return {"bsonType": "object", "required": required, "properties": properties}


async def upgrade(ctx: MigrationContext) -> None:
    db, audit_db = ctx.db, ctx.audit_db

    users = await db.create_collection("users", validator={"$jsonSchema": USERS_SCHEMA})
    await users.create_index(
        [("username", 1)],
        name="username_unique_ci",
        unique=True,
        collation=Collation(locale="en", strength=2),
    )
    await db.create_collection("households", validator={"$jsonSchema": HOUSEHOLDS_SCHEMA})
    await db.create_collection("_meta", validator={"$jsonSchema": META_SCHEMA})

    audit = await audit_db.create_collection(
        AUDIT_COLLECTION, validator={"$jsonSchema": AUDIT_SCHEMA}
    )
    await audit.create_index(
        [("target_type", 1), ("target_id", 1), ("occurred_at", 1)], name="target_time"
    )
    await audit.create_index([("occurred_at", 1)], name="occurred_at")

    for name in SCOPED_WITH_CASE:
        collection = await db.create_collection(
            name, validator={"$jsonSchema": _minimal(["_id", "household_id", "case_id"])}
        )
        await collection.create_index([("case_id", 1), ("created_at", -1)], name="case_created")
        await collection.create_index([("household_id", 1)], name="household")
    for name in SCOPED_HOUSEHOLD_ONLY:
        collection = await db.create_collection(
            name, validator={"$jsonSchema": _minimal(["_id", "household_id"])}
        )
        await collection.create_index([("household_id", 1)], name="household")
    for name in UNSCOPED:
        await db.create_collection(name, validator={"$jsonSchema": _minimal(["_id"])})

    await db["notice_drafts"].create_index(
        [("draft_id", 1), ("revision", 1)], name="draft_revision_unique", unique=True
    )
    await db["eval_overrides"].create_index([("finding_id", 1)], name="finding_unique", unique=True)


async def downgrade(ctx: MigrationContext) -> None:
    for name in [
        "users",
        "households",
        "_meta",
        *SCOPED_WITH_CASE,
        *SCOPED_HOUSEHOLD_ONLY,
        *UNSCOPED,
    ]:
        await ctx.db.drop_collection(name)
    await ctx.audit_db.drop_collection(AUDIT_COLLECTION)
