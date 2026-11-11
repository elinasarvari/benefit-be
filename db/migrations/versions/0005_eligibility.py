"""Eligibility: tighter validators for the household and rules collections (feature 003).

Replaces the minimal baseline validators of `household_members`, `household_snapshots`,
`determinations` and `rule_sets`, adds `address_state` and `seed_key` to `households` (and drops
001's `stub_result`) and `anchor_date` to `_meta`. Creates the household-only
`seeded_verifications` (ADR-003-03) and drops `household_stub_results`. Every seeded collection
may carry `synthetic: true` (FR-003-12). Runs after main's 0002–0004 (ADR-003-04). Downgrade
restores the previous validators (0003's `households`, the baseline's others), recreates
`household_stub_results` and drops the new indexes.
Documents follow `db/seeds/records.py`: UUIDs binary, dates BSON dates, money strings.
"""

from __future__ import annotations

import importlib
from typing import Any

from db.migrate import MigrationContext

UUID = {"bsonType": "binData"}
DATE = {"bsonType": "date"}
STATE = {"bsonType": "string", "pattern": "^[A-Z]{2}$"}
MONEY = {"bsonType": "string", "pattern": "^[0-9]+(\\.[0-9]+)?$"}
NULLABLE_BOOL = {"bsonType": ["bool", "null"]}
NULLABLE_STRING = {"bsonType": ["string", "null"]}
# main's `Relationship` (packages/domain/case.py), copied: an applied migration never changes.
RELATIONSHIPS = [
    "self",
    "spouse",
    "partner",
    "child",
    "parent",
    "sibling",
    "other_relative",
    "other",
]
SYNTHETIC = {"enum": [True]}
# Collections owned by earlier migrations that the seeds fill: they get the optional marker.
MARKED_EARLIER = [
    "users",
    "cases",
    "case_events",
    "coverage_periods",
    "outbox",
    "review_tasks",
    "caseworker_actions",
]
SEEDED_VERIFICATIONS = "seeded_verifications"
HOUSEHOLD_STUB_RESULTS = "household_stub_results"
FREQUENCIES = ["weekly", "biweekly", "semimonthly", "monthly"]
RULE_IDS = [f"ELIG-0{n}" for n in range(1, 7)]


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "bsonType": "object",
        "additionalProperties": False,
        "required": list(properties) if required is None else required,
        "properties": properties,
    }


def _array(items: dict[str, Any]) -> dict[str, Any]:
    return {"bsonType": "array", "items": items}


# main's 0003 households validator (with 001's `stub_result`) plus 003's fields (ADR-003-07).
_case_lifecycle = importlib.import_module("db.migrations.versions.0003_case_lifecycle")
_PRIOR_HOUSEHOLDS: dict[str, Any] = _case_lifecycle.HOUSEHOLDS_SCHEMA
HOUSEHOLDS_SCHEMA: dict[str, Any] = {
    **_PRIOR_HOUSEHOLDS,
    "properties": {
        **{k: v for k, v in _PRIOR_HOUSEHOLDS["properties"].items() if k != "stub_result"},
        "address_state": STATE,
        "seed_key": {"bsonType": "string"},
        "synthetic": SYNTHETIC,
    },
}

JOB = _object(
    {
        "job_id": UUID,
        "employer": {"bsonType": "string"},
        "declared_frequency": {"enum": FREQUENCIES},
        "declared_gross": MONEY,
    }
)

HOUSEHOLD_MEMBERS_SCHEMA = _object(
    {
        "_id": UUID,
        "household_id": UUID,
        "given_name": {"bsonType": "string"},
        "family_name": {"bsonType": "string"},
        "date_of_birth": DATE,
        "relationship": {"enum": RELATIONSHIPS},
        "address_state": STATE,
        "citizenship_attested": NULLABLE_BOOL,
        "medicare_entitled": NULLABLE_BOOL,
        "declared_zero_income": {"bsonType": "bool"},
        "jobs": _array(JOB),
        "coverage_year": {"bsonType": "int"},
        "synthetic": SYNTHETIC,
    },
    required=[
        "_id",
        "household_id",
        "given_name",
        "family_name",
        "date_of_birth",
        "relationship",
        "address_state",
        "citizenship_attested",
        "medicare_entitled",
        "declared_zero_income",
        "jobs",
        "coverage_year",
    ],
)

PAY_STUB = _object(
    {
        "document_id": UUID,
        "pay_date": DATE,
        "frequency": {"enum": FREQUENCIES},
        "gross_pay": MONEY,
    }
)

MEMBER_FACTS = _object(
    {
        "member_id": UUID,
        "relationship": {"enum": RELATIONSHIPS},
        "date_of_birth": DATE,
        "address_state": STATE,
        "citizenship_attested": NULLABLE_BOOL,
        "medicare_entitled": NULLABLE_BOOL,
        "identity_verified": NULLABLE_BOOL,
        "declared_zero_income": {"bsonType": "bool"},
        "jobs": _array(_object({"job_id": UUID, "stubs": _array(PAY_STUB)})),
    }
)

HOUSEHOLD_SNAPSHOTS_SCHEMA = _object(
    {
        "_id": UUID,
        "household_id": UUID,
        "case_id": UUID,
        "as_of_date": DATE,
        "captured_at": DATE,
        "members": _array(MEMBER_FACTS),
        "synthetic": SYNTHETIC,
    },
    required=["_id", "household_id", "case_id", "as_of_date", "captured_at", "members"],
)

SEEDED_VERIFICATIONS_SCHEMA = _object(
    {
        "_id": UUID,
        "household_id": UUID,
        "member_id": UUID,
        "identity_verified": NULLABLE_BOOL,
        "jobs": _array(_object({"job_id": UUID, "stubs": _array(PAY_STUB)})),
        "synthetic": SYNTHETIC,
    },
    required=["_id", "household_id", "member_id", "identity_verified", "jobs"],
)

MISSING_ITEM = _object(
    {
        "item": {"bsonType": "string"},
        "member_id": {"bsonType": ["binData", "null"]},
        "job_id": {"bsonType": ["binData", "null"]},
        "reason": {"bsonType": "string"},
    }
)

RULE_RESULT = _object(
    {
        "rule_id": {"enum": RULE_IDS},
        "outcome": {"enum": ["pass", "fail", "missing"]},
        "inputs": {"bsonType": "object"},
        "threshold": NULLABLE_STRING,
        "policy_ref": {"bsonType": "string"},
        "missing": _array(MISSING_ITEM),
    }
)

DETERMINATION = _object(
    {
        "result": {"enum": ["eligible", "ineligible", "cannot_determine"]},
        "as_of_date": DATE,
        "rule_set_id": NULLABLE_STRING,
        "rule_set_version": {"bsonType": ["int", "null"]},
        "engine_version": {"bsonType": "string"},
        "household_size": {"bsonType": "int", "minimum": 0},
        "household_monthly_income": {"bsonType": ["string", "null"]},
        "monthly_income_limit": {"bsonType": ["string", "null"]},
        "rule_results": _array(RULE_RESULT),
        "missing_items": _array(MISSING_ITEM),
    }
)

DETERMINATIONS_SCHEMA = _object(
    {
        "_id": UUID,
        "household_id": UUID,
        "case_id": UUID,
        "snapshot_id": UUID,
        "recorded_at": DATE,
        "determination": DETERMINATION,
        "synthetic": SYNTHETIC,
    },
    required=["_id", "household_id", "case_id", "snapshot_id", "recorded_at", "determination"],
)

RULE_SETS_SCHEMA = _object(
    {
        "_id": {"bsonType": "string"},
        "rule_set_id": {"bsonType": "string"},
        "version": {"bsonType": "int", "minimum": 1},
        "program_year": {"bsonType": "int"},
        "effective_from": DATE,
        "effective_to": DATE,
        "state": {"enum": ["IL"]},
        "age_min": {"bsonType": "int"},
        "age_max": {"bsonType": "int"},
        "fpl_percent": {"bsonType": "int"},
        "poverty_guideline": _object({"base_annual": MONEY, "per_additional_annual": MONEY}),
        "policy_refs": _object({rule: {"bsonType": "string"} for rule in RULE_IDS}),
        "synthetic": SYNTHETIC,
    },
    required=[
        "_id",
        "rule_set_id",
        "version",
        "program_year",
        "effective_from",
        "effective_to",
        "state",
        "age_min",
        "age_max",
        "fpl_percent",
        "poverty_guideline",
        "policy_refs",
    ],
)

META_SCHEMA = _object(
    {
        "_id": {"enum": ["dataset"]},
        "synthetic": {"enum": [True]},
        "seed": {"bsonType": ["int", "long"]},
        "anchor_date": DATE,
        "generator_version": {"bsonType": "string", "maxLength": 128},
        "generated_at": DATE,
    }
)

# The validators before this migration, restored on downgrade: `households` as 0003 left it,
# the others as 0001_baseline.py created them.
_BASELINE_HOUSEHOLDS = _PRIOR_HOUSEHOLDS
_BASELINE_META = _object(
    {
        "_id": {"enum": ["dataset"]},
        "synthetic": {"enum": [True]},
        "seed": {"bsonType": ["int", "long"]},
        "generator_version": {"bsonType": "string", "maxLength": 128},
        "generated_at": DATE,
    }
)


def _baseline_minimal(required: list[str]) -> dict[str, Any]:
    properties: dict[str, Any] = {name: {} for name in required if name != "_id"}
    properties.update({"household_id": UUID, "case_id": UUID})
    return {"bsonType": "object", "required": required, "properties": properties}


async def _set_validator(ctx: MigrationContext, name: str, schema: dict[str, Any]) -> None:
    await ctx.db.command("collMod", name, validator={"$jsonSchema": schema})


async def _current_schema(ctx: MigrationContext, name: str) -> dict[str, Any]:
    async for info in await ctx.db.list_collections(filter={"name": name}):
        schema: dict[str, Any] = info["options"]["validator"]["$jsonSchema"]
        return schema
    raise RuntimeError(f"collection {name} has no validator")


async def _mark_synthetic(ctx: MigrationContext, name: str, *, on: bool) -> None:
    """Add (or remove) the optional `synthetic` field of a validator an earlier migration owns."""
    schema = await _current_schema(ctx, name)
    properties = {k: v for k, v in schema["properties"].items() if k != "synthetic"}
    if on:
        properties["synthetic"] = SYNTHETIC
    await _set_validator(ctx, name, {**schema, "properties": properties})


_baseline = importlib.import_module("db.migrations.versions.0001_baseline")
DETERMINATIONS_READ = "determinations_read"  # audit action of GET .../determinations (R11)


async def _set_audit_actions(ctx: MigrationContext, actions: list[str]) -> None:
    """The audit log validator lists every action; 003 adds one (FR-003-23)."""
    schema = _baseline.AUDIT_SCHEMA
    properties = {**schema["properties"], "action": {"enum": actions}}
    await ctx.audit_db.command(
        "collMod",
        _baseline.AUDIT_COLLECTION,
        validator={"$jsonSchema": {**schema, "properties": properties}},
    )


async def upgrade(ctx: MigrationContext) -> None:
    # 001's stub field and its worker-readable copy are replaced by the real engine (ADR-003-03).
    await ctx.db["households"].update_many({}, {"$unset": {"stub_result": ""}})
    await _set_validator(ctx, "households", HOUSEHOLDS_SCHEMA)
    await ctx.db.drop_collection(HOUSEHOLD_STUB_RESULTS)
    await _set_validator(ctx, "household_members", HOUSEHOLD_MEMBERS_SCHEMA)
    await _set_validator(ctx, "household_snapshots", HOUSEHOLD_SNAPSHOTS_SCHEMA)
    await _set_validator(ctx, "determinations", DETERMINATIONS_SCHEMA)
    await _set_validator(ctx, "rule_sets", RULE_SETS_SCHEMA)
    await _set_validator(ctx, "_meta", META_SCHEMA)
    await _set_audit_actions(ctx, [*_baseline.AUDIT_ACTIONS, DETERMINATIONS_READ])
    for name in MARKED_EARLIER:
        await _mark_synthetic(ctx, name, on=True)
    await ctx.db.create_collection(
        SEEDED_VERIFICATIONS,
        validator={"$jsonSchema": SEEDED_VERIFICATIONS_SCHEMA},
        validationLevel="strict",
        validationAction="error",
    )
    await ctx.db[SEEDED_VERIFICATIONS].create_index(
        [("household_id", 1), ("member_id", 1)], name="household_member_unique", unique=True
    )
    await ctx.db["household_members"].create_index(
        [("household_id", 1), ("coverage_year", 1)], name="household_year"
    )
    await ctx.db["household_snapshots"].create_index(
        [("case_id", 1), ("as_of_date", 1)], name="case_as_of_unique", unique=True
    )
    await ctx.db["determinations"].create_index(
        [("case_id", 1), ("recorded_at", -1)], name="case_recorded"
    )
    await ctx.db["rule_sets"].create_index(
        [("rule_set_id", 1), ("version", 1)], name="rule_set_version_unique", unique=True
    )


async def downgrade(ctx: MigrationContext) -> None:
    await ctx.db["rule_sets"].drop_index("rule_set_version_unique")
    await ctx.db["determinations"].drop_index("case_recorded")
    await ctx.db["household_snapshots"].drop_index("case_as_of_unique")
    await ctx.db["household_members"].drop_index("household_year")
    await ctx.db.drop_collection(SEEDED_VERIFICATIONS)
    for name in MARKED_EARLIER:
        await _mark_synthetic(ctx, name, on=False)
    await _set_validator(ctx, "_meta", _BASELINE_META)
    await _set_audit_actions(ctx, list(_baseline.AUDIT_ACTIONS))
    await _set_validator(ctx, "rule_sets", _baseline_minimal(["_id"]))
    await _set_validator(
        ctx, "determinations", _baseline_minimal(["_id", "household_id", "case_id"])
    )
    await _set_validator(
        ctx, "household_snapshots", _baseline_minimal(["_id", "household_id", "case_id"])
    )
    await _set_validator(ctx, "household_members", _baseline_minimal(["_id", "household_id"]))
    await _set_validator(ctx, "households", _BASELINE_HOUSEHOLDS)
    # Recreate 001's worker-readable copy of the stub results, as 0003 made it.
    await ctx.db.create_collection(
        HOUSEHOLD_STUB_RESULTS,
        validator={"$jsonSchema": _case_lifecycle.HOUSEHOLD_STUB_RESULTS_SCHEMA},
        validationLevel="strict",
        validationAction="error",
    )
    await ctx.db[HOUSEHOLD_STUB_RESULTS].create_index(
        [("household_id", 1)], name="household_unique", unique=True
    )
