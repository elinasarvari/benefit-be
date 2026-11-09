"""Household isolation in the repository layer (R16, constitution VIII).

MongoDB has no row-level security, so every query on a household-scoped collection goes through
these helpers. A call with no scope raises, and there is no unscoped helper to call by mistake.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from pymongo.asynchronous.database import AsyncDatabase

from packages.domain.auth import Principal, Role, SystemCredentialClaims
from packages.platform.errors import AppError, Forbidden

SCOPED_WITH_CASE = (
    "cases",
    "case_events",
    "review_tasks",
    "caseworker_actions",
    "determinations",
    "household_snapshots",
    "outbox",
    "documents",
    "extractions",
    "agent_runs",
    "notice_drafts",
    "tool_calls",
)
SCOPED_HOUSEHOLD_ONLY = ("household_members", "coverage_periods")
HOUSEHOLD_SCOPED = frozenset(SCOPED_WITH_CASE + SCOPED_HOUSEHOLD_ONLY)


class ScopeKind(StrEnum):
    user = "user"
    system = "system"


class ScopeRequired(AppError):
    """A repository call had no scope. This is a programming error, never a client error."""


@dataclass(frozen=True)
class Scope:
    kind: ScopeKind
    role: Role | None = None
    household_id: UUID | None = None
    case_id: UUID | None = None

    @classmethod
    def for_principal(cls, principal: Principal) -> Scope:
        return cls(ScopeKind.user, role=principal.role, household_id=principal.household_id)

    @classmethod
    def for_system(cls, claims: SystemCredentialClaims) -> Scope:
        return cls(ScopeKind.system, case_id=claims.case_id)


def scoped_filter(
    scope: Scope | None, collection: str, query: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Combine the caller's filter with the scope. `query` must be built from typed values."""
    if scope is None:
        raise ScopeRequired("a scope is required for this query")
    query = query or {}
    if collection not in HOUSEHOLD_SCOPED:
        return query
    limit: dict[str, Any]
    if scope.kind is ScopeKind.system:
        if collection not in SCOPED_WITH_CASE or scope.case_id is None:
            raise Forbidden()
        limit = {"case_id": scope.case_id}
    elif scope.role is Role.applicant:
        if scope.household_id is None:
            raise Forbidden()
        limit = {"household_id": scope.household_id}
    else:
        limit = {}
    return {"$and": [query, limit]} if query and limit else (limit or query)


async def find_one(
    db: AsyncDatabase[dict[str, Any]],
    collection: str,
    scope: Scope | None,
    query: dict[str, Any] | None = None,
    projection: dict[str, int] | None = None,
) -> dict[str, Any] | None:
    return await db[collection].find_one(scoped_filter(scope, collection, query), projection)


async def find_many(
    db: AsyncDatabase[dict[str, Any]],
    collection: str,
    scope: Scope | None,
    query: dict[str, Any] | None = None,
    projection: dict[str, int] | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    cursor = db[collection].find(scoped_filter(scope, collection, query), projection).limit(limit)
    return await cursor.to_list(length=limit)
