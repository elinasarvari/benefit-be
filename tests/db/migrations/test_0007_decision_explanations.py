"""Migration 0007: the `decision_explanations` validator and index (FR-004-13, ADR-004-02)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from pymongo.errors import DuplicateKeyError, WriteError

from packages.platform.settings import Settings
from tests.integration.fixtures import open_client


def _document(**changes: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "_id": uuid.uuid4(),
        "determination_id": uuid.uuid4(),
        "case_id": uuid.uuid4(),
        "household_id": uuid.uuid4(),
        "audience": "caseworker",
        "result": "ineligible",
        "text": "Outcome: ineligible.",
        "citations": [
            {
                "rule_id": "ELIG-06",
                "section": "TM-3.1",
                "title": "Household income limit",
                "manual_version": datetime(2026, 1, 1, tzinfo=UTC),
            }
        ],
        "built_at": datetime(2026, 3, 18, 15, tzinfo=UTC),
        "synthetic": True,
    }
    document.update(changes)
    return document


async def test_FR_004_13_01_migration_0007_validator_accepts_valid_and_rejects_invalid_document(
    integration_settings: Settings, owner_url: str
) -> None:
    bad_citation = {"rule_id": "ELIG-99", "section": "x", "title": "x", "manual_version": 1}
    invalid = [
        _document(text=""),
        _document(text="x" * 10_001),
        _document(audience="auditor"),
        _document(result="approved"),
        _document(citations="none"),
        _document(citations=[bad_citation]),
        _document(built_at="2026-03-18"),
        _document(synthetic=False),
        _document(extra_field="x"),
        _document(citations=[{**_document()["citations"][0], "extra_field": "x"}]),
    ]
    missing_text = _document()
    del missing_text["text"]
    async with open_client(owner_url) as client:
        collection = client[integration_settings.MONGO_DB]["decision_explanations"]
        accepted = [_document(), _document(synthetic=None), _document(audience="applicant")]
        del accepted[1]["synthetic"]
        for document in accepted:
            await collection.insert_one(document)
        await collection.delete_many({"_id": {"$in": [d["_id"] for d in accepted]}})
        for document in [*invalid, missing_text]:
            with pytest.raises(WriteError):
                await collection.insert_one(document)


async def test_FR_004_13_02_unique_index_rejects_second_explanation_for_same_determination_and_audience(  # noqa: E501
    integration_settings: Settings, owner_url: str
) -> None:
    first = _document()
    async with open_client(owner_url) as client:
        collection = client[integration_settings.MONGO_DB]["decision_explanations"]
        try:
            await collection.insert_one(first)
            other_audience = _document(
                determination_id=first["determination_id"], audience="applicant"
            )
            await collection.insert_one(other_audience)
            with pytest.raises(DuplicateKeyError):
                await collection.insert_one(_document(determination_id=first["determination_id"]))
        finally:
            await collection.delete_many({"determination_id": first["determination_id"]})
