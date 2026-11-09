"""Clear all data and reload the synthetic data set (FR-000-02, R13).

    python -m db.seeds.reset

Refuses (exit code 3) unless the target is a local, synthetic-marked or empty database.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dbt.cli.main import dbtRunner
from pymongo.uri_parser import parse_uri

from analytics.export_mongo import export
from db.client import make_client
from db.migrate import MIGRATIONS_COLLECTION, run
from db.seeds.generate import seed
from db.seeds.users import SeedError
from packages.platform.settings import AppEnv, Settings, load_settings

LOCAL_HOSTS = {"localhost", "127.0.0.1"}


class ResetRefused(Exception):
    pass


async def guard(settings: Settings) -> None:
    if settings.APP_ENV not in (AppEnv.dev, AppEnv.prod, AppEnv.test):
        raise ResetRefused("APP_ENV is not dev, prod or test")
    hosts = {host for host, _ in parse_uri(settings.MONGO_URL_OWNER.get_secret_value())["nodelist"]}
    if not hosts <= LOCAL_HOSTS:
        raise ResetRefused(f"refusing to reset a non-local MongoDB host: {sorted(hosts)}")
    client = make_client(settings.MONGO_URL_OWNER.get_secret_value())
    try:
        db = client[settings.MONGO_DB]
        marker = await db["_meta"].find_one({"_id": "dataset"})
        has_data = False
        for name in set(await db.list_collection_names()) - {MIGRATIONS_COLLECTION, "_meta"}:
            if await db[name].estimated_document_count() > 0:
                has_data = True
    finally:
        await client.close()
    if marker is not None and marker.get("synthetic") is not True:
        raise ResetRefused("the database is not marked synthetic")
    if marker is None and has_data:
        raise ResetRefused("the database has data but no synthetic marker")


async def _drop_leftovers(settings: Settings) -> None:
    client = make_client(settings.MONGO_URL_OWNER.get_secret_value())
    try:
        for name in (settings.MONGO_DB, settings.MONGO_AUDIT_DB):
            await client.drop_database(name)
    finally:
        await client.close()


def _dbt_build(settings: Settings) -> None:
    """Run `dbt build` in this process. dbt loads .env into os.environ, so restore it after."""
    saved = dict(os.environ)
    os.environ["DUCKDB_PATH"] = settings.DUCKDB_PATH
    try:
        args = ["build", "--project-dir", "analytics", "--profiles-dir", "analytics"]
        result = dbtRunner().invoke([*args, "--log-level", "error"])
    finally:
        os.environ.clear()
        os.environ.update(saved)
    if not result.success:
        raise RuntimeError(f"dbt build failed: {result.exception}")


async def reset(settings: Settings, *, analytics: bool = True) -> None:
    await guard(settings)
    await run(settings, "down")
    await _drop_leftovers(settings)  # also clears anything a half-applied migration left behind
    await run(settings, "up")
    await asyncio.to_thread(Path(settings.DUCKDB_PATH).unlink, missing_ok=True)
    await seed(settings)
    if analytics:
        await export(settings)
        _dbt_build(settings)


def main() -> int:
    settings = load_settings()
    try:
        asyncio.run(reset(settings))
    except ResetRefused as exc:
        print(f"reset refused: {exc}", file=sys.stderr)
        return 3
    except SeedError as exc:
        print(f"seed refused: {exc}", file=sys.stderr)
        return 2
    print("reset complete: schema rebuilt and synthetic data reloaded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
