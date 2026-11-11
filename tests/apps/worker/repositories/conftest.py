from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from packages.platform.mongo import Client, make_client
from packages.platform.settings import Settings
from tests.integration.fixtures import (  # noqa: F401
    app_url,
    integration_settings,
    owner_url,
    reset_stores,
)


@pytest.fixture
async def app_client(app_url: str) -> AsyncIterator[Client]:  # noqa: F811
    """The runtime user the worker connects as."""
    client = make_client(app_url)
    try:
        yield client
    finally:
        await client.close()


@pytest.fixture
def db_name(integration_settings: Settings) -> str:  # noqa: F811
    return integration_settings.MONGO_DB


@pytest.fixture
def audit_db_name(integration_settings: Settings) -> str:  # noqa: F811
    return integration_settings.MONGO_AUDIT_DB
