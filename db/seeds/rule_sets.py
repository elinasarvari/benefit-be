"""Seeds the versioned eligibility rule sets (FR-003-17, ADR-003-04)."""

from __future__ import annotations

from typing import Any

from db.seeds.records import to_document
from packages.rules.rule_sets import RULE_SETS


def rule_set_documents() -> list[dict[str, Any]]:
    documents = []
    for rule_set in RULE_SETS:
        document = to_document(rule_set)
        document["_id"] = f"{rule_set.rule_set_id}@{rule_set.version}"
        documents.append(document)
    return documents


async def seed_rule_sets(db: Any) -> None:
    for document in rule_set_documents():
        marked = {**document, "synthetic": True}
        await db["rule_sets"].replace_one({"_id": document["_id"]}, marked, upsert=True)
