"""Shared fixtures for tests that need the local MongoDB (FR-000-17, FR-000-20).

The tests use their own database pair (`benefit_test` and `benefit_audit_test`), reset before each
run, so running them never touches the development data.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import pytest
from pymongo.errors import PyMongoError

from db.client import Client, make_client
from db.migrate import run
from packages.platform.settings import AppEnv, LlmMode, Settings, load_settings

TEST_DB = "benefit_test"
TEST_AUDIT_DB = "benefit_audit_test"


@pytest.fixture(scope="session")
def integration_settings() -> Settings:
    settings = load_settings(
        APP_ENV="test",
        LLM_MODE="fake",
        EMBEDDER="fake",
        RERANKER="fake",
        TRACING_ENABLED=False,
        MONGO_DB=TEST_DB,
        MONGO_AUDIT_DB=TEST_AUDIT_DB,
    )
    if settings.APP_ENV is not AppEnv.test or settings.LLM_MODE is not LlmMode.fake:
        pytest.fail("integration tests must run with APP_ENV=test and LLM_MODE=fake")
    return settings


async def _drop_databases(settings: Settings) -> None:
    """Start from nothing, so a half-applied migration from an earlier run can't linger."""
    client = make_client(settings.MONGO_URL_OWNER.get_secret_value())
    try:
        await client.admin.command("ping")
        for name in (settings.MONGO_DB, settings.MONGO_AUDIT_DB):
            await client.drop_database(name)
    finally:
        await client.close()


@pytest.fixture(scope="session", autouse=True)
def reset_stores(integration_settings: Settings) -> Iterator[None]:
    """Reset the test databases once before the run."""
    try:
        asyncio.run(_drop_databases(integration_settings))
    except PyMongoError as exc:
        pytest.fail(f"MongoDB is not reachable: run `make mongo-up` first ({type(exc).__name__})")
    asyncio.run(run(integration_settings, "up"))
    yield


@asynccontextmanager
async def open_client(url: str) -> AsyncIterator[Client]:
    client = make_client(url)
    try:
        yield client
    finally:
        await client.close()


@pytest.fixture
def owner_url(integration_settings: Settings) -> str:
    return integration_settings.MONGO_URL_OWNER.get_secret_value()


@pytest.fixture
def app_url(integration_settings: Settings) -> str:
    return integration_settings.MONGO_URL_APP.get_secret_value()


async def rebuild(settings: Settings) -> None:
    """Put the test databases back into a known state without going through the reset guard."""
    await _drop_databases(settings)
    await run(settings, "up")
    from db.seeds.generate import seed

    await seed(settings)
