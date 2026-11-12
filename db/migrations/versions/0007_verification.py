"""Verification (feature 002): tighter validators and indexes for `documents`, `pay_stub_details`,
`verification_results` and `reference_records`.

0006 created the last three with the minimal validator; this migration replaces all four with the
shape the feature stores (specs/002-document-verification/data-model.md). Every object is closed
(`additionalProperties: false`) and every top-level row must carry `synthetic: true` (PR-3), so a
new stored field needs a migration. Documents follow `db/seeds/records.py`: UUIDs binary, dates
BSON dates, money decimal strings. Downgrade restores 0006's minimal validators (documents:
0001's) and drops this migration's indexes.
"""

from __future__ import annotations

from typing import Any

from db.migrate import MigrationContext

UUID = {"bsonType": "binData"}
NULLABLE_UUID = {"bsonType": ["binData", "null"]}
DATE = {"bsonType": "date"}
NULLABLE_DATE = {"bsonType": ["date", "null"]}
TEXT = {"bsonType": "string", "minLength": 1, "maxLength": 100}
MONEY = {"bsonType": "string", "pattern": "^[0-9]+(\\.[0-9]+)?$"}
SYNTHETIC = {"enum": [True]}
FREQUENCIES = ["weekly", "biweekly", "semimonthly", "monthly"]
FIELDS = ["employee_name", "employer", "frequency", "gross_pay", "pay_date"]
CAUSES = ["income_mismatch", "different_person", "conflicting_stubs", "identity_mismatch"]
IDENTITIES = ["match", "no_match", "no_record"]


def _object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "bsonType": "object",
        "required": required,
        "properties": properties,
        "additionalProperties": False,
    }


def _array(items: dict[str, Any]) -> dict[str, Any]:
    return {"bsonType": "array", "items": items}


DOCUMENTS_SCHEMA = _object(
    {
        "_id": UUID,
        "case_id": UUID,
        "household_id": UUID,
        "doc_type": {"enum": ["pay_stub"]},
        "content_type": {"bsonType": "string"},
        "size_bytes": {"bsonType": ["int", "long"], "minimum": 0},
        # null until the file content arrives (PUT after the details, ADR-002-01)
        "sha256": {"bsonType": ["string", "null"], "pattern": "^[0-9a-f]{64}$"},
        "blob_key": {"bsonType": ["string", "null"]},
        "created_at": DATE,
        "content_uploaded_at": NULLABLE_DATE,
        "synthetic": SYNTHETIC,
    },
    ["_id", "case_id", "household_id", "doc_type", "created_at", "synthetic"],
)

CORRECTION = _object(
    {
        "field": {"enum": FIELDS},
        "old_value": {"bsonType": "string"},
        "new_value": {"bsonType": "string"},
        "reviewer_id": UUID,
        "reason": {"bsonType": "string", "minLength": 1, "maxLength": 500},
        "corrected_at": DATE,
        "action_id": NULLABLE_UUID,
    },
    ["field", "old_value", "new_value", "reviewer_id", "reason", "corrected_at"],
)

PAY_STUB_DETAILS_SCHEMA = _object(
    {
        "_id": UUID,
        "document_id": UUID,
        "case_id": UUID,
        "household_id": UUID,
        "member_id": NULLABLE_UUID,
        "employee_name": TEXT,
        "employer": TEXT,
        "frequency": {"enum": FREQUENCIES},
        "gross_pay": MONEY,
        "pay_date": DATE,
        "corrections": _array(CORRECTION),
        "synthetic": SYNTHETIC,
    },
    [
        "_id",
        "document_id",
        "case_id",
        "household_id",
        "employee_name",
        "employer",
        "frequency",
        "gross_pay",
        "pay_date",
        "synthetic",
    ],
)

DISMISSAL = _object(
    {
        "reviewer_id": UUID,
        "reason": {"bsonType": "string", "minLength": 1, "maxLength": 500},
        "dismissed_at": DATE,
    },
    ["reviewer_id", "reason", "dismissed_at"],
)

FINDING = _object(
    {
        "finding_id": UUID,
        "cause": {"enum": CAUSES},
        "member_id": NULLABLE_UUID,
        "job_id": NULLABLE_UUID,
        "document_id": NULLABLE_UUID,
        "identity": {"enum": [*IDENTITIES, None]},
        "declared_monthly": {"bsonType": ["string", "null"]},
        "documented_monthly": {"bsonType": ["string", "null"]},
        "conversion": {"bsonType": ["string", "null"]},
        "dismissed": {"oneOf": [{"bsonType": "null"}, DISMISSAL]},
    },
    ["finding_id", "cause"],
)

MISSING_DOCUMENT = _object(
    {
        "member_id": UUID,
        "job_id": UUID,
        "kind": {"enum": ["no_stub", "stub_too_old"]},
    },
    ["member_id", "job_id", "kind"],
)

STUB_ASSIGNMENT = _object(
    {"member_id": UUID, "job_id": UUID, "document_id": UUID},
    ["member_id", "job_id", "document_id"],
)

VERIFICATION_RESULTS_SCHEMA = _object(
    {
        "_id": UUID,
        "case_id": UUID,
        "household_id": UUID,
        "complete": {"bsonType": "bool"},
        "needs_review": {"bsonType": "bool"},
        "identity_verified": {"bsonType": ["bool", "null"]},
        "findings": _array(FINDING),
        "missing_items": _array(MISSING_DOCUMENT),
        "as_of_date": DATE,
        "stub_assignments": _array(STUB_ASSIGNMENT),
        "checked_at": DATE,
        "synthetic": SYNTHETIC,
    },
    [
        "_id",
        "case_id",
        "household_id",
        "complete",
        "needs_review",
        "findings",
        "missing_items",
        "checked_at",
        "synthetic",
    ],
)

REFERENCE_RECORDS_SCHEMA = _object(
    {
        "_id": UUID,
        "household_id": UUID,
        "member_id": UUID,
        "given_name": TEXT,
        "family_name": TEXT,
        "date_of_birth": DATE,
        "jobs": _array(
            _object({"employer": TEXT, "monthly_gross": MONEY}, ["employer", "monthly_gross"])
        ),
        "scenario_tags": _array({"bsonType": "string"}),
        "synthetic": SYNTHETIC,
    },
    [
        "_id",
        "household_id",
        "member_id",
        "given_name",
        "family_name",
        "date_of_birth",
        "jobs",
        "synthetic",
    ],
)

SCHEMAS: dict[str, dict[str, Any]] = {
    "documents": DOCUMENTS_SCHEMA,
    "pay_stub_details": PAY_STUB_DETAILS_SCHEMA,
    "verification_results": VERIFICATION_RESULTS_SCHEMA,
    "reference_records": REFERENCE_RECORDS_SCHEMA,
}

# (collection, keys, index name, unique). 0006 already made `document_unique`, `case`,
# `case_unique` and `household_member_unique`. `documents` needs none here: 0001_baseline made
# `case_created` (case_id, created_at -1; it serves both sort directions) and `household`, and
# recreating either under the same name conflicts or would drop the baseline's on downgrade.
INDEXES: list[tuple[str, list[tuple[str, int]], str, bool]] = [
    ("verification_results", [("household_id", 1)], "household", False),
    ("reference_records", [("household_id", 1), ("scenario_tags", 1)], "household_tags", False),
]

# Validators before this migration, restored on downgrade: 0001's minimal one for `documents`,
# 0006's for the others.
_BEFORE: dict[str, list[str]] = {
    "documents": ["_id", "household_id", "case_id"],
    "pay_stub_details": ["_id", "household_id", "case_id"],
    "verification_results": ["_id", "household_id", "case_id"],
    "reference_records": ["_id", "household_id"],
}


def _minimal(name: str) -> dict[str, Any]:
    required = _BEFORE[name]
    properties: dict[str, Any] = {r: UUID for r in required if r != "_id"}
    if name != "documents":
        properties["synthetic"] = SYNTHETIC
    return {"bsonType": "object", "required": required, "properties": properties}


async def _set_validator(ctx: MigrationContext, name: str, schema: dict[str, Any]) -> None:
    await ctx.db.command("collMod", name, validator={"$jsonSchema": schema})


async def upgrade(ctx: MigrationContext) -> None:
    for name, schema in SCHEMAS.items():
        await _set_validator(ctx, name, schema)
    for name, keys, index_name, unique in INDEXES:
        await ctx.db[name].create_index(keys, name=index_name, unique=unique)


async def downgrade(ctx: MigrationContext) -> None:
    for name, _keys, index_name, _unique in INDEXES:
        await ctx.db[name].drop_index(index_name)
    for name in SCHEMAS:
        await _set_validator(ctx, name, _minimal(name))
