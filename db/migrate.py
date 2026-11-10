"""Versioned MongoDB migrations (KD-11).

Each module in `db/migrations/versions/` exposes `upgrade(ctx)` and `downgrade(ctx)`. Applied
versions are recorded in the `_migrations` collection of the main database.

    python -m db.migrate up [version|head]
    python -m db.migrate down [version|base]
"""

from __future__ import annotations

import asyncio
import importlib
import pkgutil
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import ModuleType

from pymongo.asynchronous.database import AsyncDatabase

from db import migrations as migrations_package
from db.client import Client, make_client
from packages.platform.clock import SystemClock
from packages.platform.settings import Settings, load_settings

MIGRATIONS_COLLECTION = "_migrations"


@dataclass
class MigrationContext:
    client: Client
    db: AsyncDatabase  # type: ignore[type-arg]
    audit_db: AsyncDatabase  # type: ignore[type-arg]


def discover() -> list[tuple[str, ModuleType]]:
    versions_package = importlib.import_module(f"{migrations_package.__name__}.versions")
    found = [
        (info.name, importlib.import_module(f"{versions_package.__name__}.{info.name}"))
        for info in pkgutil.iter_modules(versions_package.__path__)
    ]
    return sorted(found)


async def _applied(ctx: MigrationContext) -> set[str]:
    return {doc["_id"] async for doc in ctx.db[MIGRATIONS_COLLECTION].find({}, {"_id": 1})}


async def up(ctx: MigrationContext, target: str = "head") -> list[str]:
    applied, ran = await _applied(ctx), []
    for version, module in discover():
        if version in applied:
            continue
        step: Callable[[MigrationContext], Awaitable[None]] = module.upgrade
        await step(ctx)
        await ctx.db[MIGRATIONS_COLLECTION].insert_one(
            {"_id": version, "applied_at": SystemClock().now()}
        )
        ran.append(version)
        if target == version:
            break
    return ran


async def down(ctx: MigrationContext, target: str = "base") -> list[str]:
    applied, ran = await _applied(ctx), []
    for version, module in reversed(discover()):
        if version not in applied or version == target:
            if version == target:
                break
            continue
        step: Callable[[MigrationContext], Awaitable[None]] = module.downgrade
        await step(ctx)
        await ctx.db[MIGRATIONS_COLLECTION].delete_one({"_id": version})
        ran.append(version)
    return ran


async def run(settings: Settings, direction: str, target: str | None = None) -> list[str]:
    client = make_client(settings.MONGO_URL_OWNER.get_secret_value())
    try:
        ctx = MigrationContext(client, client[settings.MONGO_DB], client[settings.MONGO_AUDIT_DB])
        if direction == "up":
            return await up(ctx, target or "head")
        return await down(ctx, target or "base")
    finally:
        await client.close()


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("up", "down"):
        print("usage: python -m db.migrate up|down [target]", file=sys.stderr)
        return 2
    ran = asyncio.run(run(load_settings(), argv[0], argv[1] if len(argv) > 1 else None))
    print(f"{argv[0]}: {', '.join(ran) if ran else 'nothing to do'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
