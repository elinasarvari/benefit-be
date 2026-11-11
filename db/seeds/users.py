"""The seeded demo accounts. Usernames and display names are made up (PR-3)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from db.seeds.ids import HOUSEHOLD_A, HOUSEHOLD_B, HOUSEHOLD_C, seeded_id
from packages.domain.auth import Role
from packages.platform.auth.passwords import hash_password

DEMO_ACCOUNTS: list[tuple[str, str, Role, uuid.UUID | None]] = [
    ("applicant1", "Applicant One", Role.applicant, HOUSEHOLD_A),
    ("applicant2", "Applicant Two", Role.applicant, HOUSEHOLD_B),
    ("applicant3", "Applicant Three", Role.applicant, HOUSEHOLD_C),
    ("caseworker1", "Caseworker One", Role.caseworker, None),
    ("supervisor1", "Supervisor One", Role.supervisor, None),
]


class SeedError(Exception):
    pass


async def seed_users(
    db: AsyncDatabase[dict[str, Any]], password: str, created_at: datetime
) -> None:
    """All accounts share one password, `DEMO_PASSWORD`, stored only as an argon2id hash."""
    if not password:
        raise SeedError("DEMO_PASSWORD is not set; run make setup or set it in .env")
    password_hash = hash_password(password)
    documents = []
    for username, display_name, role, household_id in DEMO_ACCOUNTS:
        document: dict[str, Any] = {
            "_id": seeded_id(f"user-{username}"),
            "username": username,
            "display_name": display_name,
            "password_hash": password_hash,
            "role": role.value,
            "created_at": created_at,
            "synthetic": True,
        }
        if household_id is not None:
            document["household_id"] = household_id
        documents.append(document)
    await db["users"].insert_many(documents)
