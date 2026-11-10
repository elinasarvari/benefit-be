from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from pymongo.errors import WriteError

from db.seeds.reset import reset
from packages.platform.settings import Settings
from tests.integration.fixtures import open_client


async def test_FR_000_14_each_store_has_synthetic_marker(
    integration_settings: Settings, owner_url: str, tmp_path: Path
) -> None:
    settings = integration_settings.model_copy(update={"DUCKDB_PATH": str(tmp_path / "r.duckdb")})

    await reset(settings)

    async with open_client(owner_url) as client:
        db = client[settings.MONGO_DB]
        marker = await db["_meta"].find_one({"_id": "dataset"})
        with pytest.raises(WriteError):
            await db["_meta"].update_one({"_id": "dataset"}, {"$set": {"synthetic": False}})
    assert marker is not None
    assert marker["synthetic"] is True
    connection = duckdb.connect(settings.DUCKDB_PATH)
    try:
        rows = connection.execute("select synthetic from meta.dataset_metadata").fetchall()
    finally:
        connection.close()
    assert rows == [(True,)]
