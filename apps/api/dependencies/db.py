from __future__ import annotations

from typing import Any

from fastapi import Request
from pymongo.asynchronous.database import AsyncDatabase

from apps.api.identity_local import LocalIdentityProvider
from apps.api.repositories.users import UserRepository
from packages.platform.audit import AuditWriter, MongoAuditWriter
from packages.platform.auth.identity import IdentityProvider
from packages.platform.clock import Clock, SystemClock
from packages.platform.mongo import Client, make_client
from packages.platform.settings import Settings


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_mongo(request: Request) -> Client:
    """One client per process, created on first use, with bounded waits (FR-000-13)."""
    client: Client | None = getattr(request.app.state, "mongo", None)
    if client is None:
        settings = get_settings(request)
        client = make_client(settings.MONGO_URL_APP.get_secret_value())
        request.app.state.mongo = client
    return client


def get_database(request: Request) -> AsyncDatabase[dict[str, Any]]:
    return get_mongo(request)[get_settings(request).MONGO_DB]


def get_identity(request: Request) -> IdentityProvider:
    return LocalIdentityProvider(UserRepository(get_database(request)))


def get_audit_writer(request: Request) -> AuditWriter:
    return MongoAuditWriter(get_mongo(request), get_settings(request).MONGO_AUDIT_DB)


def get_clock() -> Clock:
    return SystemClock()
