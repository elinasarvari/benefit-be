from __future__ import annotations

from typing import Protocol
from uuid import UUID

from packages.domain.auth import UserPublic


class IdentityProvider(Protocol):
    """Who a caller is. Real single sign-on could replace the local one by configuration."""

    async def authenticate(self, username: str, password: str) -> UserPublic | None: ...

    async def get_user(self, user_id: UUID) -> UserPublic | None: ...
