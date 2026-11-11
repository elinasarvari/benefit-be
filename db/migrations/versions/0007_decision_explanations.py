"""Decision explanations: the document validator (feature 004a, ADR-004-02, FR-004-13).

Replaces the minimal validator 0006 gave `decision_explanations` with the stored document of
`packages/domain/explanation.py`: `text` 1..10 000 characters, `audience` and `result` from their
enums, `citations` an array, `built_at` a date, `synthetic` true or absent. The index
`determination_audience_unique` from 0006 stays. Downgrade restores 0006's minimal validator.
Enums are copied, not imported: an applied migration never changes.
"""

from __future__ import annotations

from typing import Any

from db.migrate import MigrationContext

COLLECTION = "decision_explanations"
UUID = {"bsonType": "binData"}
CITATION = {
    "bsonType": "object",
    "additionalProperties": False,
    "required": ["rule_id", "section", "title", "manual_version"],
    "properties": {
        "rule_id": {"enum": ["ELIG-01", "ELIG-02", "ELIG-03", "ELIG-04", "ELIG-05", "ELIG-06"]},
        "section": {"bsonType": "string", "minLength": 1, "maxLength": 20},
        "title": {"bsonType": "string", "minLength": 1, "maxLength": 200},
        "manual_version": {"bsonType": "date"},
    },
}
REQUIRED = [
    "_id",
    "determination_id",
    "case_id",
    "household_id",
    "audience",
    "result",
    "text",
    "citations",
    "built_at",
]
SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "additionalProperties": False,
    "required": REQUIRED,
    "properties": {
        "_id": UUID,
        "determination_id": UUID,
        "case_id": UUID,
        "household_id": UUID,
        "audience": {"enum": ["applicant", "caseworker"]},
        "result": {"enum": ["eligible", "ineligible", "cannot_determine"]},
        "text": {"bsonType": "string", "minLength": 1, "maxLength": 10_000},
        "citations": {"bsonType": "array", "items": CITATION},
        "built_at": {"bsonType": "date"},
        "synthetic": {"enum": [True]},
    },
}
MINIMAL: dict[str, Any] = {
    "bsonType": "object",
    "required": ["_id", "household_id", "case_id"],
    "properties": {
        "household_id": UUID,
        "case_id": UUID,
        "synthetic": {"enum": [True]},
    },
}


async def _set_validator(ctx: MigrationContext, schema: dict[str, Any]) -> None:
    await ctx.db.command("collMod", COLLECTION, validator={"$jsonSchema": schema})


async def upgrade(ctx: MigrationContext) -> None:
    await _set_validator(ctx, SCHEMA)


async def downgrade(ctx: MigrationContext) -> None:
    await _set_validator(ctx, MINIMAL)
