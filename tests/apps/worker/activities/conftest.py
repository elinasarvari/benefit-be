"""Fixtures for the activity tests that run against the local MongoDB (pay stub check).

The other tests in this folder need no database, so the reset is opt-in here (`stores`), unlike
`tests/integration/fixtures.py`'s autouse one.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator

import pytest

from db.migrate import run
from packages.platform.mongo import Client, make_client
from packages.platform.settings import Settings
from tests.integration.fixtures import (  # noqa: F401
    _drop_databases,
    app_url,
    integration_settings,
    owner_url,
)


@pytest.fixture(scope="session")
def stores(integration_settings: Settings) -> Iterator[None]:  # noqa: F811
    """Fresh, migrated test databases, once per run."""
    asyncio.run(_drop_databases(integration_settings))
    asyncio.run(run(integration_settings, "up"))
    yield


@pytest.fixture
async def mongo(app_url: str) -> AsyncIterator[Client]:  # noqa: F811
    """The runtime user the worker connects as."""
    client = make_client(app_url)
    try:
        yield client
    finally:
        await client.close()


@pytest.fixture
def db_name(integration_settings: Settings, stores: None) -> str:  # noqa: F811
    return integration_settings.MONGO_DB
