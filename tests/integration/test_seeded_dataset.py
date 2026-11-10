"""Seeded data is usable by every consumer lane (FR-003-13, FR-003-22, SC-003-04).

Test sets are written by `db/seeds/testsets.py` (feature 003 data lane) into
`db/seeds/generated/testsets/`; until that generator exists these tests fail on purpose.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from db.seeds.generate import GENERATED_DIR
from packages.domain.household import HouseholdSnapshot
from packages.platform.settings import Settings
from tests.integration.fixtures import open_client, rebuild

TESTSETS = GENERATED_DIR / "testsets"
ITEM_FIELDS: dict[str, set[str]] = {
    "known_answers": {"case_key", "as_of_date", "snapshot", "expected_result", "expected_failed"},
    "pay_stub_extractions": {"document_id", "path", "poor_quality", "expected"},
    "redeterminations_due": {
        "household_id",
        "coverage_period_id",
        "coverage_end",
        "redetermination_start",
        "expected_path",
    },
    "expected_metrics": {"metric", "period", "expected_value", "numerator", "denominator"},
}


def _load(name: str) -> dict[str, Any]:
    path: Path = TESTSETS / f"{name}.json"
    assert path.is_file(), f"{path} was not generated"
    loaded: dict[str, Any] = json.loads(path.read_text())
    return loaded


@pytest.mark.parametrize("name", sorted(ITEM_FIELDS))
async def test_FR_003_13_consumer_lanes_load_testsets_with_domain_models(
    name: str, integration_settings: Settings, owner_url: str
) -> None:
    await rebuild(integration_settings)
    async with open_client(owner_url) as client:
        meta = await client[integration_settings.MONGO_DB]["_meta"].find_one({"_id": "dataset"})
    assert meta is not None

    testset = _load(name)

    assert testset["synthetic"] is True
    assert testset["seed"] == meta["seed"]
    assert date.fromisoformat(testset["anchor_date"]) == meta["anchor_date"].date()
    assert testset["generator_version"] == meta["generator_version"]
    assert testset["items"]
    for item in testset["items"]:
        assert ITEM_FIELDS[name] <= item.keys()
        if name == "known_answers":
            HouseholdSnapshot.model_validate(item["snapshot"])
            date.fromisoformat(item["as_of_date"])


async def test_FR_003_22_every_seeded_row_passes_its_owning_collection_validator(
    integration_settings: Settings, owner_url: str
) -> None:
    await rebuild(integration_settings)

    async with open_client(owner_url) as client:
        db = client[integration_settings.MONGO_DB]
        invalid: dict[str, int] = {}
        checked = 0
        async for info in await db.list_collections():
            validator = info.get("options", {}).get("validator", {}).get("$jsonSchema")
            if validator is None:
                continue
            checked += 1
            count = await db[info["name"]].count_documents({"$nor": [{"$jsonSchema": validator}]})
            if count:
                invalid[info["name"]] = count

    assert checked > 0
    assert not invalid, invalid
