"""Case screens (001): the `applications` collection and the `cases.open_deadline` index.

`applications` holds the submitted form, written once in the opening transaction (ADR-001-08);
`cases.open_deadline` serves the default case list and the applicant list (ADR-001-07). The
`0001_baseline` registry lists stay frozen: this migration declares its own collection (KD-11).
"""

from __future__ import annotations

from typing import Any

from db.migrate import MigrationContext

APPLICATIONS = "applications"
OPEN_DEADLINE_INDEX = "open_deadline"

UUID: dict[str, Any] = {"bsonType": "binData"}
DATE: dict[str, Any] = {"bsonType": "date"}


def _object(required: list[str], properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "bsonType": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


def _enum(values: list[str]) -> dict[str, Any]:
    return {"enum": list(values)}


JOB = _object(
    ["employer", "pay_frequency", "declared_monthly_gross"],
    {
        "employer": {"bsonType": "string", "minLength": 1, "maxLength": 100},
        "pay_frequency": _enum(["weekly", "biweekly", "semimonthly", "monthly"]),
        # Decimal128 or a 2-place string (JSON string in the contract), never a float.
        "declared_monthly_gross": {"bsonType": ["decimal", "string"]},
    },
)

MEMBER = _object(
    [
        "full_name",
        "date_of_birth",
        "relationship",
        "citizenship_status",
        "medicare_entitled",
        "zero_income",
        "jobs",
    ],
    {
        "full_name": {"bsonType": "string", "minLength": 1, "maxLength": 100},
        "date_of_birth": DATE,
        "relationship": _enum(
            [
                "self",
                "spouse",
                "partner",
                "child",
                "parent",
                "sibling",
                "other_relative",
                "other",
            ]
        ),
        "citizenship_status": _enum(["us_citizen", "qualifying_immigrant", "neither"]),
        "medicare_entitled": {"bsonType": "bool"},
        "zero_income": {"bsonType": "bool"},
        "jobs": {"bsonType": "array", "maxItems": 5, "items": JOB},
    },
)

ADDRESS = _object(
    ["line1", "city", "state", "postal_code"],
    {
        "line1": {"bsonType": "string", "minLength": 1, "maxLength": 100},
        "line2": {"bsonType": ["string", "null"], "maxLength": 100},
        "city": {"bsonType": "string", "minLength": 1, "maxLength": 60},
        "state": _enum(["IL"]),
        "postal_code": {"bsonType": "string", "pattern": r"^\d{5}$"},
    },
)

FORM = _object(
    ["members", "address"],
    {
        "members": {"bsonType": "array", "minItems": 1, "maxItems": 10, "items": MEMBER},
        "address": ADDRESS,
    },
)

APPLICATIONS_SCHEMA = _object(
    [
        "_id",
        "case_id",
        "household_id",
        "submission_reference",
        "form",
        "submitted_at",
        "synthetic",
    ],
    {
        "_id": UUID,
        "case_id": UUID,
        "household_id": UUID,
        "submission_reference": {"bsonType": "string", "minLength": 1, "maxLength": 128},
        "form": FORM,
        "submitted_at": DATE,
        "synthetic": _enum([True]),  # type: ignore[list-item]
        "created_at": DATE,
        "updated_at": DATE,
    },
)


async def upgrade(ctx: MigrationContext) -> None:
    applications = await ctx.db.create_collection(
        APPLICATIONS,
        validator={"$jsonSchema": APPLICATIONS_SCHEMA},
        validationLevel="strict",
        validationAction="error",
    )
    # The case a household submitted is looked up by `case_id` (= `_id`), already indexed.
    del applications
    await ctx.db["cases"].create_index(
        [("open", 1), ("decision_deadline", 1), ("_id", 1)], name=OPEN_DEADLINE_INDEX
    )


async def downgrade(ctx: MigrationContext) -> None:
    await ctx.db["cases"].drop_index(OPEN_DEADLINE_INDEX)
    await ctx.db.drop_collection(APPLICATIONS)
