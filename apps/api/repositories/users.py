from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.collation import Collation

from packages.domain.auth import LoginRequest, Role, UserPublic

CASE_INSENSITIVE = Collation(locale="en", strength=2)
PUBLIC_FIELDS = {"_id": 1, "username": 1, "display_name": 1, "role": 1, "household_id": 1}


@dataclass(frozen=True)
class LoginRecord:
    user: UserPublic
    password_hash: str


def _to_public(doc: dict[str, Any]) -> UserPublic:
    return UserPublic(
        id=doc["_id"],
        username=doc["username"],
        display_name=doc["display_name"],
        role=Role(doc["role"]),
        household_id=doc.get("household_id"),
    )


class UserRepository:
    def __init__(self, db: AsyncDatabase[dict[str, Any]]) -> None:
        self._users = db["users"]

    async def lookup_for_login(self, username: str) -> LoginRecord | None:
        # The username is validated against the same pattern as the login request, so an
        # operator such as {"$ne": null} can never reach the filter.
        LoginRequest.model_validate({"username": username, "password": "x"})
        doc = await self._users.find_one(
            {"username": username},
            {**PUBLIC_FIELDS, "password_hash": 1},
            collation=CASE_INSENSITIVE,
        )
        return LoginRecord(_to_public(doc), doc["password_hash"]) if doc else None

    async def display_names(self, ids: list[UUID]) -> dict[UUID, str]:
        """Display names of the given users in one `$in` read; unknown ids are left out."""
        if not ids:
            return {}
        cursor = self._users.find({"_id": {"$in": list(set(ids))}}, {"_id": 1, "display_name": 1})
        return {doc["_id"]: doc["display_name"] for doc in await cursor.to_list(length=len(ids))}

    async def get_public(self, user_id: UUID) -> UserPublic | None:
        doc = await self._users.find_one({"_id": user_id}, PUBLIC_FIELDS)
        return _to_public(doc) if doc else None
