"""Redeterminations (007): the renewal link and the coverage-continued event.

Edits the live validators in place (like 0006), so markers added by earlier migrations (the
`synthetic` field of 0005) are kept:

- `cases`: optional `coverage_period_id`, the period a redetermination renews (ADR-007-03).
- `coverage_periods`: optional `renews_coverage_period_id` (ADR-007-03, FR-007-05). Its `status`
  enum already holds `renewed` and `continued_pending_review` (0003).
- `case_events`: `kind` adds `coverage_continued` (ADR-007-04).
- `audit_log`: `action` adds `redetermination_started_by_schedule` (ADR-007-02).

No new collection and no new index. Downgrade removes the fields and values again.
"""

from __future__ import annotations

import importlib
from typing import Any

from db.migrate import MigrationContext

_baseline = importlib.import_module("db.migrations.versions.0001_baseline")

UUID_OR_NULL: dict[str, Any] = {"bsonType": ["binData", "null"]}
CASE_LINK = ("cases", "coverage_period_id")
PERIOD_LINK = ("coverage_periods", "renews_coverage_period_id")
NEW_EVENT_KIND = "coverage_continued"
NEW_AUDIT_ACTION = "redetermination_started_by_schedule"


async def _schema(db: Any, name: str) -> dict[str, Any]:
    async for info in await db.list_collections(filter={"name": name}):
        schema: dict[str, Any] = info["options"]["validator"]["$jsonSchema"]
        return schema
    raise RuntimeError(f"collection {name} has no validator")


async def _set_properties(db: Any, name: str, change: dict[str, Any], *, drop: list[str]) -> None:
    schema = await _schema(db, name)
    properties = {k: v for k, v in schema["properties"].items() if k not in drop}
    properties.update(change)
    await db.command(
        "collMod", name, validator={"$jsonSchema": {**schema, "properties": properties}}
    )


async def _set_enum(db: Any, name: str, field: str, value: str, *, present: bool) -> None:
    schema = await _schema(db, name)
    values = [v for v in schema["properties"][field]["enum"] if v != value]
    if present:
        values.append(value)
    await _set_properties(
        db, name, {field: {**schema["properties"][field], "enum": values}}, drop=[]
    )


async def upgrade(ctx: MigrationContext) -> None:
    for name, field in (CASE_LINK, PERIOD_LINK):
        await _set_properties(ctx.db, name, {field: UUID_OR_NULL}, drop=[])
    await _set_enum(ctx.db, "case_events", "kind", NEW_EVENT_KIND, present=True)
    await _set_enum(
        ctx.audit_db, _baseline.AUDIT_COLLECTION, "action", NEW_AUDIT_ACTION, present=True
    )


async def downgrade(ctx: MigrationContext) -> None:
    await _set_enum(
        ctx.audit_db, _baseline.AUDIT_COLLECTION, "action", NEW_AUDIT_ACTION, present=False
    )
    await _set_enum(ctx.db, "case_events", "kind", NEW_EVENT_KIND, present=False)
    for name, field in (CASE_LINK, PERIOD_LINK):
        await _set_properties(ctx.db, name, {}, drop=[field])
