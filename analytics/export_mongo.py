"""Copy pseudonymised data from MongoDB into DuckDB staging tables (KD-15).

Only the synthetic-data marker is exported in feature 000. Later features add projections that
carry pseudonymous keys only, never names, addresses, dates of birth or document contents.

    python -m analytics.export_mongo
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import duckdb

from db.client import make_client
from packages.platform.settings import Settings, load_settings


async def export(settings: Settings) -> None:
    client = make_client(settings.MONGO_URL_APP.get_secret_value())
    try:
        marker = await client[settings.MONGO_DB]["_meta"].find_one(
            {"_id": "dataset"},
            {"synthetic": 1, "seed": 1, "generator_version": 1, "generated_at": 1},
        )
    finally:
        await client.close()
    if marker is None:
        raise RuntimeError("the _meta marker is missing; run the seed first")

    path = Path(settings.DUCKDB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(path))
    try:
        connection.execute("create schema if not exists staging")
        connection.execute(
            "create or replace table staging.dataset_metadata "
            "(synthetic boolean, seed bigint, generator_version varchar, generated_at timestamptz)"
        )
        connection.execute(
            "insert into staging.dataset_metadata values (?, ?, ?, ?)",
            [
                marker["synthetic"],
                marker["seed"],
                marker["generator_version"],
                marker["generated_at"],
            ],
        )
    finally:
        connection.close()


def main() -> int:
    asyncio.run(export(load_settings()))
    print("exported the synthetic marker to DuckDB staging")
    return 0


if __name__ == "__main__":
    sys.exit(main())
