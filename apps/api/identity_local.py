from __future__ import annotations

import asyncio
from uuid import UUID

from pydantic import ValidationError

from apps.api.repositories.users import UserRepository
from packages.domain.auth import UserPublic
from packages.platform.auth.passwords import DUMMY_HASH, verify_password


class LocalIdentityProvider:
    """Signs in the seeded demo accounts (IDENTITY_PROVIDER=local)."""

    def __init__(self, users: UserRepository) -> None:
        self._users = users

    async def authenticate(self, username: str, password: str) -> UserPublic | None:
        try:
            record = await self._users.lookup_for_login(username)
        except ValidationError:
            record = None
        # An unknown user still costs one password check, so timing doesn't reveal which exist.
        stored = record.password_hash if record else DUMMY_HASH
        matches = await asyncio.to_thread(verify_password, stored, password)
        return record.user if record and matches else None

    async def get_user(self, user_id: UUID) -> UserPublic | None:
        return await self._users.get_public(user_id)
