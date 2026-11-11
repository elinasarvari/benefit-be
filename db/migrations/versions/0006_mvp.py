"""MVP collections (specs/mvp-contracts.md section 5): pay stub details and verification results
(002), reference records (002, seed task S-01), notices and applicant responses (011), and
decision explanations (004a, 011). Also adds 002's two new review causes (`income_mismatch`,
`conflicting_stubs`) to the `review_tasks` validator, which lists every cause (0002); the
validator is read and edited in place so 0005's `synthetic` marker is kept.

Each starts with the baseline's minimal validator (`_id`, `household_id`, and `case_id` for case
collections) and may carry `synthetic: true` (FR-000-14). The owning feature tightens its own
validator in a later migration once its document shape is built. Downgrade drops them.
"""

from __future__ import annotations

from typing import Any

from db.migrate import MigrationContext

UUID = {"bsonType": "binData"}
SYNTHETIC = {"enum": [True]}

CREATED_WITH_CASE = [
    "pay_stub_details",
    "verification_results",
    "notices",
    "applicant_responses",
    "decision_explanations",
]
CREATED_HOUSEHOLD_ONLY = ["reference_records"]

# (collection, keys, index name, unique)
INDEXES: list[tuple[str, list[tuple[str, int]], str, bool]] = [
    ("pay_stub_details", [("document_id", 1)], "document_unique", True),
    ("pay_stub_details", [("case_id", 1)], "case", False),
    ("verification_results", [("case_id", 1)], "case_unique", True),
    (
        "notices",
        [("household_id", 1), ("status", 1), ("created_at", -1)],
        "household_status",
        False,
    ),
    ("notices", [("case_id", 1), ("created_at", -1)], "case_created", False),
    ("applicant_responses", [("case_id", 1), ("notice_id", 1)], "case_notice", False),
    (
        "decision_explanations",
        [("determination_id", 1), ("audience", 1)],
        "determination_audience_unique",
        True,
    ),
    ("decision_explanations", [("case_id", 1)], "case", False),
    ("reference_records", [("household_id", 1), ("member_id", 1)], "household_member_unique", True),
]


REVIEW_TASKS = "review_tasks"
NEW_REVIEW_CAUSES = ["income_mismatch", "conflicting_stubs"]


def _minimal(required: list[str]) -> dict[str, Any]:
    properties: dict[str, Any] = {name: UUID for name in required if name != "_id"}
    properties["synthetic"] = SYNTHETIC
    return {"bsonType": "object", "required": required, "properties": properties}


async def _review_tasks_schema(ctx: MigrationContext) -> dict[str, Any]:
    async for info in await ctx.db.list_collections(filter={"name": REVIEW_TASKS}):
        schema: dict[str, Any] = info["options"]["validator"]["$jsonSchema"]
        return schema
    raise RuntimeError(f"collection {REVIEW_TASKS} has no validator")


async def _set_review_causes(ctx: MigrationContext, *, add: bool) -> None:
    schema = await _review_tasks_schema(ctx)
    causes = [c for c in schema["properties"]["cause"]["enum"] if c not in NEW_REVIEW_CAUSES]
    if add:
        causes += NEW_REVIEW_CAUSES
    properties = {**schema["properties"], "cause": {"enum": causes}}
    await ctx.db.command(
        "collMod", REVIEW_TASKS, validator={"$jsonSchema": {**schema, "properties": properties}}
    )


async def upgrade(ctx: MigrationContext) -> None:
    await _set_review_causes(ctx, add=True)
    for name in CREATED_WITH_CASE:
        await ctx.db.create_collection(
            name, validator={"$jsonSchema": _minimal(["_id", "household_id", "case_id"])}
        )
    for name in CREATED_HOUSEHOLD_ONLY:
        await ctx.db.create_collection(
            name, validator={"$jsonSchema": _minimal(["_id", "household_id"])}
        )
    for name, keys, index_name, unique in INDEXES:
        await ctx.db[name].create_index(keys, name=index_name, unique=unique)


async def downgrade(ctx: MigrationContext) -> None:
    await _set_review_causes(ctx, add=False)
    for name in [*CREATED_WITH_CASE, *CREATED_HOUSEHOLD_ONLY]:
        await ctx.db.drop_collection(name)
