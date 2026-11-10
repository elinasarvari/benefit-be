from __future__ import annotations

import importlib
import uuid
from datetime import UTC, datetime

import pytest

from packages.domain.auth import Principal, Role, SystemCredentialClaims
from packages.platform.errors import Forbidden
from packages.platform.scope import (
    HOUSEHOLD_SCOPED,
    UNSCOPED,
    Scope,
    ScopeKind,
    ScopeRequired,
    scoped_filter,
)

HOUSEHOLD, CASE = uuid.uuid4(), uuid.uuid4()


def scope_for(role: Role, household: uuid.UUID | None = HOUSEHOLD) -> Scope:
    return Scope.for_principal(
        Principal(
            user_id=uuid.uuid4(),
            role=role,
            household_id=household,
            session_expires_at=datetime.now(UTC),
        )
    )


def test_FR_000_19_applicant_filter_is_always_anded_with_the_household() -> None:
    hostile = {"household_id": {"$ne": None}}

    combined = scoped_filter(scope_for(Role.applicant), "cases", hostile)

    assert combined == {"$and": [hostile, {"household_id": HOUSEHOLD}]}


@pytest.mark.parametrize(
    ("collection", "expected"),
    [("households", {"_id": HOUSEHOLD}), ("users", {"household_id": HOUSEHOLD})],
)
def test_FR_000_19_applicant_sees_only_their_own_household_and_users(
    collection: str, expected: dict[str, object]
) -> None:
    assert scoped_filter(scope_for(Role.applicant), collection) == expected


def test_FR_000_19_staff_roles_are_not_household_filtered() -> None:
    for role in (Role.caseworker, Role.supervisor):
        assert scoped_filter(scope_for(role, None), "cases", {"x": 1}) == {"x": 1}


def test_FR_000_19_a_user_scope_without_a_role_is_refused() -> None:
    with pytest.raises(Forbidden):
        scoped_filter(Scope(ScopeKind.user), "cases")


def test_FR_000_19_an_unknown_collection_is_refused_not_passed_through() -> None:
    with pytest.raises(ScopeRequired, match="unknown collection"):
        scoped_filter(scope_for(Role.applicant), "casess")


def test_FR_000_19_system_scope_is_limited_to_its_case_and_to_case_collections() -> None:
    claims = SystemCredentialClaims(case_id=CASE, workflow_id="w", step="s", iat=0, exp=1, jti="j")
    system = Scope.for_system(claims)

    assert scoped_filter(system, "documents") == {"case_id": CASE}
    for forbidden in ("household_members", "users", "households"):
        with pytest.raises(Forbidden):
            scoped_filter(system, forbidden)


def test_FR_000_19_registry_covers_every_collection_the_migrations_create() -> None:
    baseline = importlib.import_module("db.migrations.versions.0001_baseline")
    created = set(baseline.SCOPED_WITH_CASE) | set(baseline.SCOPED_HOUSEHOLD_ONLY)

    assert created == set(HOUSEHOLD_SCOPED)
    assert set(baseline.UNSCOPED) <= set(UNSCOPED)
