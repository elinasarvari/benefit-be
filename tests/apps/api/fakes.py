"""Hand-written fakes for the identity provider and clock, used across API tests."""

from __future__ import annotations

import uuid
from uuid import UUID

from packages.domain.auth import Role, UserPublic
from packages.platform.auth.passwords import hash_password, verify_password

PASSWORD = "demo-password-123"
HOUSEHOLD = uuid.uuid4()


def make_users() -> dict[str, tuple[UserPublic, str]]:
    users = {}
    for username, role in (
        ("applicant1", Role.applicant),
        ("caseworker1", Role.caseworker),
        ("supervisor1", Role.supervisor),
    ):
        user = UserPublic(
            id=uuid.uuid4(),
            username=username,
            display_name=f"{role.value.title()} One",
            role=role,
            household_id=HOUSEHOLD if role is Role.applicant else None,
        )
        users[username] = (user, hash_password(PASSWORD))
    return users


class FakeIdentity:
    def __init__(self) -> None:
        self.users = make_users()

    async def authenticate(self, username: str, password: str) -> UserPublic | None:
        record = self.users.get(username)
        if record and verify_password(record[1], password):
            return record[0]
        return None

    async def get_user(self, user_id: UUID) -> UserPublic | None:
        return next((u for u, _ in self.users.values() if u.id == user_id), None)
