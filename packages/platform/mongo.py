"""One way to create MongoDB clients, so UUIDs and dates behave the same everywhere."""

from __future__ import annotations

from datetime import UTC
from typing import Any

from pymongo import AsyncMongoClient

Client = AsyncMongoClient[dict[str, Any]]

CONNECT_TIMEOUT_MS = 5000


def make_client(url: str) -> Client:
    """Standard UUID encoding, timezone-aware UTC datetimes, and bounded waits (FR-000-13)."""
    return AsyncMongoClient(
        url,
        uuidRepresentation="standard",
        tz_aware=True,
        tzinfo=UTC,
        serverSelectionTimeoutMS=CONNECT_TIMEOUT_MS,
        connectTimeoutMS=CONNECT_TIMEOUT_MS,
    )
