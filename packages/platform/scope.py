"""Household isolation in the repository layer (R16, constitution VIII).

MongoDB has no row-level security, so every query on a household-scoped collection goes through
these helpers. A call with no scope raises, and there is no unscoped helper to call by mistake.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.database import AsyncDatabase

from packages.domain.auth import Principal, Role, SystemCredentialClaims
from packages.platform.errors import AppError, Forbidden

SCOPED_WITH_CASE = (
    "cases",
    "case_events",
    "review_tasks",
    "caseworker_actions",
    "applications",
    "determinations",
    "household_snapshots",
    "outbox",
    "documents",
    "extractions",
    "agent_runs",
    "notice_drafts",
    "tool_calls",
    # MVP contracts (migration 0006)
    "pay_stub_details",
    "verification_results",
    "notices",
    "applicant_responses",
    "decision_explanations",
)
SCOPED_HOUSEHOLD_ONLY = (
    "household_members",
    "coverage_periods",
    "seeded_verifications",
    "reference_records",
)
HOUSEHOLD_SCOPED = frozenset(SCOPED_WITH_CASE + SCOPED_HOUSEHOLD_ONLY)
# Collections with no household data. Any name not in this registry is refused, so a typo or a
# new collection can never skip the household filter by accident.
UNSCOPED = frozenset({"rule_sets", "policy_passages", "eval_overrides", "_meta", "_migrations"})
MAX_LIMIT = 500


class ScopeKind(StrEnum):
    user = "user"
    system = "system"
    # Worker activities: bound to the one case and household in the workflow input (ADR-001-06).
    worker = "worker"


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

    @classmethod
    def for_worker(cls, case_id: UUID, household_id: UUID) -> Scope:
        return cls(ScopeKind.worker, case_id=case_id, household_id=household_id)


def _worker_limit(scope: Scope, collection: str) -> dict[str, Any]:
    """Case collections by case_id, household-only collections by household_id (ADR-001-06)."""
    if collection in SCOPED_WITH_CASE and scope.case_id is not None:
        return {"case_id": scope.case_id}
    if collection in SCOPED_HOUSEHOLD_ONLY and scope.household_id is not None:
        return {"household_id": scope.household_id}
    raise Forbidden()


def _own_household(scope: Scope) -> dict[str, Any]:
    if scope.household_id is None:
        raise Forbidden()
    return {"household_id": scope.household_id}


def scoped_filter(
    scope: Scope | None, collection: str, query: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Combine the caller's filter with the scope. `query` must be built from typed values."""
    if scope is None:
        raise ScopeRequired("a scope is required for this query")
    query = query or {}
    if collection in UNSCOPED:
        return query
    limit: dict[str, Any]
    if collection in HOUSEHOLD_SCOPED:
        if scope.kind is ScopeKind.worker:
            limit = _worker_limit(scope, collection)
        elif scope.kind is ScopeKind.system:
            if collection not in SCOPED_WITH_CASE or scope.case_id is None:
                raise Forbidden()
            limit = {"case_id": scope.case_id}
        elif scope.role is None:
            raise Forbidden()
        else:
            limit = _own_household(scope) if scope.role is Role.applicant else {}
    elif collection in ("households", "users"):
        if scope.kind is not ScopeKind.user or scope.role is None:
            raise Forbidden()
        if scope.role is Role.applicant:
            household = _own_household(scope)["household_id"]
            limit = (
                {"_id": household} if collection == "households" else {"household_id": household}
            )
        else:
            limit = {}
    else:
        raise ScopeRequired(f"unknown collection {collection!r}: add it to the scope registry")
    return {"$and": [query, limit]} if query and limit else (limit or query)


async def find_one(
    db: AsyncDatabase[dict[str, Any]],
    collection: str,
    scope: Scope | None,
    query: dict[str, Any] | None,
    *,
    projection: dict[str, int],
    session: AsyncClientSession | None = None,
) -> dict[str, Any] | None:
    return await db[collection].find_one(
        scoped_filter(scope, collection, query), projection, session=session
    )


async def insert_one(
    db: AsyncDatabase[dict[str, Any]],
    collection: str,
    scope: Scope | None,
    document: dict[str, Any],
    *,
    session: AsyncClientSession | None = None,
) -> None:
    """Insert a document only if it falls inside the scope (same limits as a read)."""
    limit = scoped_filter(scope, collection)
    if any(document.get(field) != value for field, value in limit.items()):
        raise Forbidden()
    await db[collection].insert_one(document, session=session)


async def find_many(
    db: AsyncDatabase[dict[str, Any]],
    collection: str,
    scope: Scope | None,
    query: dict[str, Any] | None = None,
    *,
    projection: dict[str, int],
    limit: int = 100,
    after_id: Any = None,
) -> list[dict[str, Any]]:
    """One page, ordered by `_id`. Pass the last `_id` of the previous page as `after_id`."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    limit = min(limit, MAX_LIMIT)
    window = {"_id": {"$gt": after_id}} if after_id is not None else {}
    combined = {"$and": [query, window]} if query and window else (query or window)
    cursor = (
        db[collection]
        .find(scoped_filter(scope, collection, combined), projection)
        .sort("_id", 1)
        .limit(limit)
    )
    return await cursor.to_list(length=limit)
