"""Eligibility: tighter validators for the household and rules collections (feature 003).

Replaces the minimal baseline validators of `household_members`, `household_snapshots`,
`determinations` and `rule_sets`, adds `address_state` and `seed_key` to `households` and
`anchor_date` to `_meta`. Runs after main's 0002–0004 (ADR-003-07). Downgrade restores the
previous validators (0003's `households`, the baseline's others) and drops the new indexes.
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
RELATIONSHIPS = ["self", "spouse", "child", "other"]
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
        **_PRIOR_HOUSEHOLDS["properties"],
        "address_state": STATE,
        "seed_key": {"bsonType": "string"},
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
    }
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
        "captured_at": DATE,
        "members": _array(MEMBER_FACTS),
    }
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
    }
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
    }
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


async def upgrade(ctx: MigrationContext) -> None:
    await _set_validator(ctx, "households", HOUSEHOLDS_SCHEMA)
    await _set_validator(ctx, "household_members", HOUSEHOLD_MEMBERS_SCHEMA)
    await _set_validator(ctx, "household_snapshots", HOUSEHOLD_SNAPSHOTS_SCHEMA)
    await _set_validator(ctx, "determinations", DETERMINATIONS_SCHEMA)
    await _set_validator(ctx, "rule_sets", RULE_SETS_SCHEMA)
    await _set_validator(ctx, "_meta", META_SCHEMA)
    await ctx.db["household_members"].create_index(
        [("household_id", 1), ("coverage_year", 1)], name="household_year"
    )
    await ctx.db["rule_sets"].create_index(
        [("rule_set_id", 1), ("version", 1)], name="rule_set_version_unique", unique=True
    )


async def downgrade(ctx: MigrationContext) -> None:
    await ctx.db["rule_sets"].drop_index("rule_set_version_unique")
    await ctx.db["household_members"].drop_index("household_year")
    await _set_validator(ctx, "_meta", _BASELINE_META)
    await _set_validator(ctx, "rule_sets", _baseline_minimal(["_id"]))
    await _set_validator(
        ctx, "determinations", _baseline_minimal(["_id", "household_id", "case_id"])
    )
    await _set_validator(
        ctx, "household_snapshots", _baseline_minimal(["_id", "household_id", "case_id"])
    )
    await _set_validator(ctx, "household_members", _baseline_minimal(["_id", "household_id"]))
    await _set_validator(ctx, "households", _BASELINE_HOUSEHOLDS)
