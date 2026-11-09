"""Initiate the replica set and create the database users. Safe to run repeatedly.

First run: no users exist yet, so the localhost exception allows `replSetInitiate` and the first
`createUser` without credentials. Later runs authenticate as `benefit_owner`.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any
from urllib.parse import unquote

from pymongo import AsyncMongoClient
from pymongo.errors import OperationFailure
from pymongo.uri_parser import parse_uri

from db.collections import ADMIN_DB, APP_USER, AUDIT_COLLECTION, AUDIT_ROLE, OWNER_USER
from packages.platform.settings import load_settings

Client = AsyncMongoClient[dict[str, Any]]
REPLICA_SET = "rs0"
HOST = "localhost:27017"
NOT_YET_INITIALIZED = 94
UNAUTHORIZED = 13
ALREADY_EXISTS = {51003, 51002}  # user / role already exists


def _password(url: str) -> str:
    password = parse_uri(url)["password"]
    if not password:
        raise ValueError("the MongoDB URL must contain a password")
    return unquote(password)


async def _ensure_replica_set(client: Client) -> None:
    admin = client[ADMIN_DB]
    try:
        await admin.command("replSetGetStatus")
    except OperationFailure as exc:
        if exc.code != NOT_YET_INITIALIZED:
            raise
        config = {"_id": REPLICA_SET, "members": [{"_id": 0, "host": HOST}]}
        await admin.command("replSetInitiate", config)
    for _ in range(60):
        if (await admin.command("hello")).get("isWritablePrimary"):
            return
        await asyncio.sleep(0.5)
    raise RuntimeError("replica set did not elect a primary")


async def _create_owner(client: Client, password: str) -> None:
    """The localhost exception only allows a first user that can manage users, so start minimal."""
    roles = [{"role": "userAdminAnyDatabase", "db": ADMIN_DB}]
    try:
        await client[ADMIN_DB].command("createUser", OWNER_USER, pwd=password, roles=roles)
    except OperationFailure as exc:
        if exc.code not in ALREADY_EXISTS:
            raise


def database_pairs(db: str, audit_db: str) -> list[tuple[str, str]]:
    """The runtime databases plus a separate pair the integration tests reset freely."""
    return [(db, audit_db), (f"{db}_test", f"{audit_db}_test")]


async def _provision(url_owner: str, url_app: str, db: str, audit_db: str) -> None:
    owner_password, app_password = _password(url_owner), _password(url_app)
    pairs = database_pairs(db, audit_db)

    bootstrap: Client = AsyncMongoClient(f"mongodb://{HOST}/?directConnection=true")
    try:
        try:
            await _ensure_replica_set(bootstrap)
        except OperationFailure as exc:
            if exc.code != UNAUTHORIZED:  # users exist already: the owner connection below works
                raise
        try:
            await _create_owner(bootstrap, owner_password)
        except OperationFailure as exc:
            if exc.code != UNAUTHORIZED:
                raise
    finally:
        await bootstrap.close()

    owner: Client = AsyncMongoClient(url_owner)
    try:
        admin = owner[ADMIN_DB]
        owner_roles = [
            *({"role": "dbOwner", "db": name} for pair in pairs for name in pair),
            {"role": "clusterManager", "db": ADMIN_DB},
        ]
        await admin.command("grantRolesToUser", OWNER_USER, roles=owner_roles)
        privileges = [
            {
                "resource": {"db": audit_name, "collection": AUDIT_COLLECTION},
                "actions": ["insert", "find"],
            }
            for _, audit_name in pairs
        ]
        try:
            await admin.command("createRole", AUDIT_ROLE, privileges=privileges, roles=[])
        except OperationFailure as exc:
            if exc.code not in ALREADY_EXISTS:
                raise
            await admin.command("updateRole", AUDIT_ROLE, privileges=privileges)
        app_roles = [
            *({"role": "readWrite", "db": name} for name, _ in pairs),
            {"role": AUDIT_ROLE, "db": ADMIN_DB},
        ]
        try:
            await admin.command("createUser", APP_USER, pwd=app_password, roles=app_roles)
        except OperationFailure as exc:
            if exc.code not in ALREADY_EXISTS:
                raise
            await admin.command("updateUser", APP_USER, pwd=app_password, roles=app_roles)
    finally:
        await owner.close()


def main() -> int:
    settings = load_settings()
    asyncio.run(
        _provision(
            settings.MONGO_URL_OWNER.get_secret_value(),
            settings.MONGO_URL_APP.get_secret_value(),
            settings.MONGO_DB,
            settings.MONGO_AUDIT_DB,
        )
    )
    print("MongoDB provisioned: replica set rs0, users benefit_owner and benefit_app.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
