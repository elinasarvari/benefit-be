"""Reference lookup (FR-002-22): identity and per-job income from the synthetic data set.

Contract stub (MVP contracts PR): `StubReferenceRecords` answers "match, no jobs" for everyone.
002 adds the MongoDB implementation over `reference_records` (seed task S-01), read through the
worker scope like `verified_facts.py`.
"""

from __future__ import annotations

from typing import Protocol

from packages.domain.verification import IdentityMatch, ReferenceAnswer, ReferenceLookup
from packages.platform.scope import Scope


class ReferenceRecords(Protocol):
    async def lookup(self, scope: Scope, request: ReferenceLookup) -> ReferenceAnswer: ...


class StubReferenceRecords:
    async def lookup(self, scope: Scope, request: ReferenceLookup) -> ReferenceAnswer:
        return ReferenceAnswer(member_id=request.member_id, identity=IdentityMatch.match)
