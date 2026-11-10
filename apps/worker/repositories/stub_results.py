"""Reads the seeded stub result of one household for the 001 determination stub (research R1).

The worker scope may not read `households` (ADR-001-06), so the seed also writes each result to
`household_stub_results`, a household-only collection filtered on the scope's `household_id`.
003 removes this reader together with the stub.
"""

from __future__ import annotations

from typing import Any

from packages.domain.case import DeterminationOutcome
from packages.platform.mongo import Client
from packages.platform.scope import Scope, find_one

HOUSEHOLD_STUB_RESULTS = "household_stub_results"


class MongoStubResults:
    """The household's seeded result, or `default` when the household has none."""

    def __init__(
        self,
        client: Client,
        db_name: str,
        default: DeterminationOutcome = DeterminationOutcome.eligible,
    ) -> None:
        self._db = client[db_name]
        self._default = default

    async def outcome_for(self, scope: Scope) -> DeterminationOutcome:
        document: dict[str, Any] | None = await find_one(
            self._db, HOUSEHOLD_STUB_RESULTS, scope, None, projection={"stub_result": 1}
        )
        if document is None:
            return self._default
        return DeterminationOutcome(document["stub_result"])
