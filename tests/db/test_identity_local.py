from __future__ import annotations

import pytest

from apps.api.identity_local import LocalIdentityProvider
from apps.api.repositories.users import UserRepository
from packages.domain.auth import Role
from packages.platform.settings import Settings
from tests.integration.fixtures import open_client, rebuild


@pytest.fixture
async def identity(integration_settings: Settings, app_url: str):  # type: ignore[no-untyped-def]
    await rebuild(integration_settings)
    async with open_client(app_url) as client:
        yield LocalIdentityProvider(UserRepository(client[integration_settings.MONGO_DB]))


async def test_FR_000_21_demo_account_signs_in_with_the_seeded_password(
    identity: LocalIdentityProvider, integration_settings: Settings
) -> None:
    password = integration_settings.DEMO_PASSWORD.get_secret_value()

    user = await identity.authenticate("caseworker1", password)

    assert user is not None
    assert user.role is Role.caseworker
    assert user.household_id is None


async def test_FR_000_21_username_lookup_is_case_insensitive(
    identity: LocalIdentityProvider, integration_settings: Settings
) -> None:
    password = integration_settings.DEMO_PASSWORD.get_secret_value()

    user = await identity.authenticate("Applicant1", password)

    assert user is not None
    assert user.household_id is not None


@pytest.mark.parametrize(
    ("username", "password"),
    [("caseworker1", "wrong-password"), ("nobody", "anything-at-all-1"), ("caseworker1", "")],
)
async def test_FR_000_21_wrong_credentials_return_none(
    identity: LocalIdentityProvider, username: str, password: str
) -> None:
    assert await identity.authenticate(username, password) is None


async def test_FR_000_21_a_filter_operator_as_username_cannot_sign_anyone_in(
    identity: LocalIdentityProvider, integration_settings: Settings
) -> None:
    password = integration_settings.DEMO_PASSWORD.get_secret_value()

    assert await identity.authenticate('{"$ne": null}', password) is None
    assert await identity.authenticate("$ne", password) is None
