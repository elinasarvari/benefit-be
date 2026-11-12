from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr
from pymongo.errors import WriteError

from db.seeds.generate import seed
from db.seeds.ids import HOUSEHOLD_A, HOUSEHOLD_B, HOUSEHOLD_C, HOUSEHOLD_D
from db.seeds.reset import reset
from db.seeds.users import SeedError
from packages.domain.auth import Role
from packages.platform.auth.passwords import verify_password
from packages.platform.settings import Settings
from tests.integration.fixtures import open_client, rebuild


async def users_by_name(settings: Settings, url: str) -> dict[str, dict[str, object]]:
    async with open_client(url) as client:
        docs = [d async for d in client[settings.MONGO_DB]["users"].find()]
    return {d["username"]: d for d in docs}


async def test_FR_000_05_seed_creates_each_role(
    integration_settings: Settings, owner_url: str
) -> None:
    await reset(integration_settings, analytics=False)

    users = await users_by_name(integration_settings, owner_url)

    assert {n: u["role"] for n, u in users.items()} == {
        "applicant1": Role.applicant.value,
        "applicant2": Role.applicant.value,
        "applicant3": Role.applicant.value,
        "applicant4": Role.applicant.value,
        "caseworker1": Role.caseworker.value,
        "supervisor1": Role.supervisor.value,
    }
    assert users["applicant1"]["household_id"] == HOUSEHOLD_A
    assert users["applicant2"]["household_id"] == HOUSEHOLD_B
    assert users["applicant3"]["household_id"] == HOUSEHOLD_C
    assert users["applicant4"]["household_id"] == HOUSEHOLD_D
    assert "household_id" not in users["caseworker1"]


async def test_FR_000_21_password_stored_as_argon2id(
    integration_settings: Settings, owner_url: str
) -> None:
    await reset(integration_settings, analytics=False)
    password = integration_settings.DEMO_PASSWORD.get_secret_value()

    users = await users_by_name(integration_settings, owner_url)

    for user in users.values():
        stored = str(user["password_hash"])
        assert stored.startswith("$argon2id$")
        assert password not in stored
        assert verify_password(stored, password)


async def test_FR_000_21_seed_refuses_without_demo_password(
    integration_settings: Settings, owner_url: str
) -> None:
    await rebuild(integration_settings)
    blank = integration_settings.model_copy(update={"DEMO_PASSWORD": SecretStr("")})
    before = await users_by_name(integration_settings, owner_url)

    with pytest.raises(SeedError, match="DEMO_PASSWORD"):
        await seed(blank)

    assert await users_by_name(integration_settings, owner_url) == before


@pytest.mark.parametrize(
    ("role", "household", "accepted"),
    [
        ("applicant", None, False),
        ("applicant", uuid.uuid4(), True),
        ("caseworker", uuid.uuid4(), False),
        ("caseworker", None, True),
    ],
)
async def test_FR_000_05_validator_requires_a_household_only_for_applicants(
    integration_settings: Settings,
    owner_url: str,
    role: str,
    household: uuid.UUID | None,
    accepted: bool,
) -> None:
    document: dict[str, object] = {
        "_id": uuid.uuid4(),
        "username": f"probe-{uuid.uuid4().hex[:8]}",
        "display_name": "Probe",
        "password_hash": "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA",
        "role": role,
        "created_at": datetime.now(UTC),
    }
    if household is not None:
        document["household_id"] = household
    async with open_client(owner_url) as client:
        users = client[integration_settings.MONGO_DB]["users"]
        if accepted:
            await users.insert_one(document)
            await users.delete_one({"_id": document["_id"]})
        else:
            with pytest.raises(WriteError):
                await users.insert_one(document)


async def test_FR_000_21_validator_rejects_a_plaintext_password(
    integration_settings: Settings, owner_url: str
) -> None:
    document = {
        "_id": uuid.uuid4(),
        "username": "plain",
        "display_name": "Plain",
        "password_hash": "hunter2",
        "role": "caseworker",
        "created_at": datetime.now(UTC),
    }
    async with open_client(owner_url) as client:
        with pytest.raises(WriteError):
            await client[integration_settings.MONGO_DB]["users"].insert_one(document)
