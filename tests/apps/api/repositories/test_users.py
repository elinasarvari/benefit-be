"""display_names against the local MongoDB (001, TC-96; R18)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from apps.api.repositories.users import UserRepository
from packages.platform.settings import Settings
from tests.integration.fixtures import open_client

pytestmark = pytest.mark.integration

CW1, CW2 = uuid.uuid4(), uuid.uuid4()
HASH = "$argon2id$v=19$m=65536,t=3,p=4$c3ludGg$c3ludGhldGljLWhhc2g"


def staff(user_id: uuid.UUID, username: str, display_name: str) -> dict[str, Any]:
    return {
        "_id": user_id,
        "username": username,
        "display_name": display_name,
        "password_hash": HASH,
        "role": "caseworker",
        "household_id": None,
        "created_at": datetime.now(UTC),
    }


class _Spy:
    """Counts the queries the repository sends."""

    def __init__(self, users: Any) -> None:
        self._users = users
        self.finds = 0

    def find(self, *args: Any, **kwargs: Any) -> Any:
        self.finds += 1
        return self._users.find(*args, **kwargs)


async def test_FR_001_10_display_names_resolves_ids_in_one_read(
    integration_settings: Settings, owner_url: str
) -> None:
    async with open_client(owner_url) as client:
        db = client[integration_settings.MONGO_DB]
        await db["users"].delete_many({"username": {"$in": ["cw-synth-1", "cw-synth-2"]}})
        await db["users"].insert_many(
            [staff(CW1, "cw-synth-1", "Casey Synth"), staff(CW2, "cw-synth-2", "Riley Synth")]
        )
        try:
            repo = UserRepository(db)
            spy = _Spy(repo._users)
            repo._users = spy  # type: ignore[assignment]

            names = await repo.display_names([CW1, CW2, uuid.uuid4(), CW1])
            empty = await repo.display_names([])

            assert names == {CW1: "Casey Synth", CW2: "Riley Synth"}  # unknown id left out
            assert empty == {}
            assert spy.finds == 1  # one `$in` read, none for an empty list
        finally:
            await db["users"].delete_many({"_id": {"$in": [CW1, CW2]}})
