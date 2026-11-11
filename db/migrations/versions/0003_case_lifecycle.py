"""Case lifecycle (001): tighten the case collections' validators and add their indexes.

Tightens the minimal `0001_baseline` validators of `cases`, `case_events`, `coverage_periods` and
`outbox` to the fields and enums of
specs/001-case-lifecycle/data-model.md, lets `households` carry the synthetic 001 stub field, and
creates `household_stub_results`, the copy of that field the worker may read (ADR-001-06).

`review_tasks` and `caseworker_actions` are left as `0002_review_queue` (010) shapes them: 001
writes those collections in 010's shape (ADR-001-11).

The enum values are copied here, not imported from `packages/domain/case.py`: an applied migration
never changes (KD-11), so a later feature that adds a value adds it in its own migration.

Calendar dates (`received_on`, `decision_deadline`, `decided_on`, `start_date`, `end_date`) are
stored as BSON dates at midnight UTC (database.md).
"""

from __future__ import annotations

import importlib
from typing import Any

from db.migrate import MigrationContext

_baseline = importlib.import_module("db.migrations.versions.0001_baseline")

UUID: dict[str, Any] = {"bsonType": "binData"}
UUID_OR_NULL: dict[str, Any] = {"bsonType": ["binData", "null"]}
DATE: dict[str, Any] = {"bsonType": "date"}
DATE_OR_NULL: dict[str, Any] = {"bsonType": ["date", "null"]}
INT: dict[str, Any] = {"bsonType": ["int", "long"]}
BOOL: dict[str, Any] = {"bsonType": "bool"}
CODE: dict[str, Any] = {"bsonType": "string", "maxLength": 64, "pattern": "^[a-z0-9_]+$"}

CASE_TYPES = ["application", "redetermination"]
CASE_STATES = [
    "submitted",
    "verifying",
    "awaiting_applicant",
    "determining",
    "under_review",
    "decided",
]
CASE_OUTCOMES = [
    "approved",
    "denied",
    "renewed_automatically",
    "renewed_after_response",
    "ended_by_caseworker",
]
DENIAL_REASONS = ["income_over_limit", "other_rule_not_met", "missing_information"]
EVENT_KINDS = [
    "state_changed",
    "review_task_opened",
    "caseworker_action",
    "action_rejected",
    "notice_requested",
    "coverage_started",
    "demo_action",
]
COVERAGE_STATUSES = ["active", "continued_pending_review", "renewed", "ended"]
STUB_RESULTS = ["eligible", "ineligible", "cannot_determine"]
NOTICE_TYPES = ["approval", "denial"]  # 011 widens this to the FR-011-06 list


def _enum(values: list[str], *, nullable: bool = False) -> dict[str, Any]:
    return {"enum": [*values, None] if nullable else list(values)}


def _schema(required: list[str], properties: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "bsonType": "object",
        "additionalProperties": False,
        "required": required,
        "properties": {"created_at": DATE, "updated_at": DATE, **properties},
        **extra,
    }


CLOCK: dict[str, Any] = {
    "bsonType": "object",
    "additionalProperties": False,
    "required": ["real_start", "case_start", "scale", "offset_seconds"],
    "properties": {
        "real_start": DATE,
        "case_start": DATE,
        "scale": {**INT, "minimum": 1},
        "offset_seconds": INT,
    },
}

OVERRIDE: dict[str, Any] = {
    "bsonType": ["object", "null"],
    "additionalProperties": False,
    "required": ["reviewer_id", "reason", "determination_id"],
    "properties": {
        "reviewer_id": UUID,
        "reason": {"bsonType": "string", "minLength": 1, "maxLength": 500},
        "determination_id": UUID_OR_NULL,
    },
}

CASES_SCHEMA = _schema(
    [
        "_id",
        "household_id",
        "case_id",
        "case_type",
        "submission_reference",
        "state",
        "open",
        "received_on",
        "decision_deadline",
        "clock",
        "last_seq",
        "created_at",
        "updated_at",
    ],
    {
        "_id": UUID,
        "household_id": UUID,
        "case_id": UUID,
        "case_type": _enum(CASE_TYPES),
        "submission_reference": {"bsonType": "string", "minLength": 1, "maxLength": 128},
        "state": _enum(CASE_STATES),
        "open": BOOL,
        "received_on": DATE,
        "decision_deadline": DATE,
        "clock": CLOCK,
        "outcome": _enum(CASE_OUTCOMES, nullable=True),
        "denial_reason": _enum(DENIAL_REASONS, nullable=True),
        "override": OVERRIDE,
        "decided_on": DATE_OR_NULL,
        "last_seq": {**INT, "minimum": 0},
    },
    # `open` is false exactly when the case is decided (FR-001-01, ADR-001-02).
    anyOf=[
        {"properties": {"state": {"enum": ["decided"]}, "open": {"enum": [False]}}},
        {
            "properties": {
                "state": {"enum": [s for s in CASE_STATES if s != "decided"]},
                "open": {"enum": [True]},
            }
        },
    ],
)

CASE_EVENTS_SCHEMA = _schema(
    [
        "_id",
        "case_id",
        "household_id",
        "seq",
        "kind",
        "case_time",
        "real_time",
        "actor_type",
        "reason_code",
    ],
    {
        "_id": UUID,
        "case_id": UUID,
        "household_id": UUID,
        "seq": {**INT, "minimum": 1},
        "kind": _enum(EVENT_KINDS),
        "from_state": _enum(CASE_STATES, nullable=True),
        "to_state": _enum(CASE_STATES, nullable=True),
        "case_time": DATE,
        "real_time": DATE,
        "actor_type": _enum(["system", "user"]),
        "actor_id": UUID_OR_NULL,
        "reason_code": CODE,  # enum-backed code, never free text
        "review_task_id": UUID_OR_NULL,
    },
)

COVERAGE_PERIODS_SCHEMA = _schema(
    ["_id", "household_id", "case_id", "start_date", "end_date", "status"],
    {
        "_id": UUID,
        "household_id": UUID,
        "case_id": UUID,
        "start_date": DATE,
        "end_date": DATE,
        "status": _enum(COVERAGE_STATUSES),
    },
)

OUTBOX_SCHEMA = _schema(
    ["_id", "case_id", "household_id", "notice_type", "status", "requested_case_time"],
    {
        "_id": UUID,
        "case_id": UUID,
        "household_id": UUID,
        "notice_type": _enum(NOTICE_TYPES),
        "status": _enum(["requested"]),
        "requested_case_time": DATE,
    },
)

HOUSEHOLDS_SCHEMA: dict[str, Any] = {
    **_baseline.HOUSEHOLDS_SCHEMA,
    "properties": {
        **_baseline.HOUSEHOLDS_SCHEMA["properties"],
        "stub_result": _enum(STUB_RESULTS),  # synthetic, drives the 001 stubs (research R1)
    },
}

# The households' stub results again, keyed by `household_id`, so the worker can read them under
# its household-bound scope without reading `households` (ADR-001-06, research R1). 003 drops it.
HOUSEHOLD_STUB_RESULTS = "household_stub_results"
HOUSEHOLD_STUB_RESULTS_SCHEMA = _schema(
    ["_id", "household_id", "stub_result"],
    {
        "_id": UUID,
        "household_id": UUID,
        "stub_result": _enum(STUB_RESULTS),
    },
)
# Household-only collections this migration creates (the scope registry must list them).
CREATED_HOUSEHOLD_ONLY = [HOUSEHOLD_STUB_RESULTS]

SCHEMAS: dict[str, dict[str, Any]] = {
    "cases": CASES_SCHEMA,
    "case_events": CASE_EVENTS_SCHEMA,
    "coverage_periods": COVERAGE_PERIODS_SCHEMA,
    "outbox": OUTBOX_SCHEMA,
    "households": HOUSEHOLDS_SCHEMA,
}

# (collection, index name, keys, options)
INDEXES: list[tuple[str, str, list[tuple[str, int]], dict[str, Any]]] = [
    ("cases", "submission_reference_unique", [("submission_reference", 1)], {"unique": True}),
    (
        "cases",
        "household_open_unique",
        [("household_id", 1)],
        {"unique": True, "partialFilterExpression": {"open": True}},
    ),
    ("cases", "state_deadline", [("state", 1), ("decision_deadline", 1)], {}),
    ("case_events", "case_seq_unique", [("case_id", 1), ("seq", 1)], {"unique": True}),
    ("coverage_periods", "household_end_date", [("household_id", 1), ("end_date", 1)], {}),
]


def _baseline_schema(name: str) -> dict[str, Any]:
    if name == "households":
        return dict(_baseline.HOUSEHOLDS_SCHEMA)
    required = (
        ["_id", "household_id"]
        if name in _baseline.SCOPED_HOUSEHOLD_ONLY
        else ["_id", "household_id", "case_id"]
    )
    schema: dict[str, Any] = _baseline._minimal(required)
    return schema


async def _set_validator(ctx: MigrationContext, name: str, schema: dict[str, Any]) -> None:
    await ctx.db.command(
        "collMod",
        name,
        validator={"$jsonSchema": schema},
        validationLevel="strict",
        validationAction="error",
    )


async def upgrade(ctx: MigrationContext) -> None:
    for name, schema in SCHEMAS.items():
        await _set_validator(ctx, name, schema)
    for collection, index_name, keys, options in INDEXES:
        await ctx.db[collection].create_index(keys, name=index_name, **options)
    stubs = await ctx.db.create_collection(
        HOUSEHOLD_STUB_RESULTS,
        validator={"$jsonSchema": HOUSEHOLD_STUB_RESULTS_SCHEMA},
        validationLevel="strict",
        validationAction="error",
    )
    await stubs.create_index([("household_id", 1)], name="household_unique", unique=True)


async def downgrade(ctx: MigrationContext) -> None:
    await ctx.db.drop_collection(HOUSEHOLD_STUB_RESULTS)
    for collection, index_name, _keys, _options in reversed(INDEXES):
        await ctx.db[collection].drop_index(index_name)
    for name in SCHEMAS:
        await _set_validator(ctx, name, _baseline_schema(name))
