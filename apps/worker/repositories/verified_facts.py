"""Verified identity and pay stub facts of a household's members (FR-003-16, FR-003-17).

`VerifiedFacts` is the seam 002 (pay stub extraction) and 005 (identity) will implement. Until they
land, `SeededVerifications` reads the household-only collection `seeded_verifications` through the
worker scope, which filters on the scope's `household_id` (ADR-003-03).
"""

from __future__ import annotations

from typing import Protocol

from packages.domain.household import VerifiedMemberFacts
from packages.platform.mongo import Client
from packages.platform.scope import Scope, find_many

SEEDED_VERIFICATIONS = "seeded_verifications"
FIELDS = {"household_id": 1, "member_id": 1, "identity_verified": 1, "jobs": 1}


class VerifiedFacts(Protocol):
    async def for_household(self, scope: Scope) -> list[VerifiedMemberFacts]: ...


class SeededVerifications:
    def __init__(self, client: Client, db_name: str) -> None:
        self._db = client[db_name]

    async def for_household(self, scope: Scope) -> list[VerifiedMemberFacts]:
        documents = await find_many(
            self._db, SEEDED_VERIFICATIONS, scope, projection={"_id": 0, **FIELDS}
        )
        return [VerifiedMemberFacts.model_validate(document) for document in documents]
