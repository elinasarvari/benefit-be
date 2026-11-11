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
    insert_one,
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
    case_lifecycle = importlib.import_module("db.migrations.versions.0003_case_lifecycle")
    case_screens = importlib.import_module("db.migrations.versions.0004_case_screens")
    created = (
        set(baseline.SCOPED_WITH_CASE)
        | set(baseline.SCOPED_HOUSEHOLD_ONLY)
        | set(case_lifecycle.CREATED_HOUSEHOLD_ONLY)
        | {case_screens.APPLICATIONS}
        | {"seeded_verifications"}  # created by 0005 (ADR-003-03)
    ) - {"household_stub_results"}  # dropped by 0005

    assert created == set(HOUSEHOLD_SCOPED)
    assert set(baseline.UNSCOPED) <= set(UNSCOPED)


# --- Worker scope (001, ADR-001-06) ------------------------------------------------------------

C1, H1 = uuid.uuid4(), uuid.uuid4()


def test_FR_001_23_worker_scope_filters_coverage_by_household_and_refuses_households() -> None:
    worker = Scope.for_worker(C1, H1)

    assert worker.kind is ScopeKind.worker
    assert scoped_filter(worker, "coverage_periods", {"end_date": {"$gte": "2026-03-17"}}) == {
        "$and": [{"end_date": {"$gte": "2026-03-17"}}, {"household_id": H1}]
    }
    assert scoped_filter(worker, "seeded_verifications") == {"household_id": H1}
    assert "household_stub_results" not in HOUSEHOLD_SCOPED  # dropped by 0005 (ADR-003-03)
    for forbidden in ("households", "users"):
        with pytest.raises(Forbidden):
            scoped_filter(worker, forbidden)
    with pytest.raises(Forbidden):
        scoped_filter(Scope(ScopeKind.worker, case_id=C1), "coverage_periods")


def test_FR_001_10_worker_scope_filters_case_collections_by_case_id() -> None:
    worker = Scope.for_worker(C1, H1)

    for collection in (
        "cases",
        "case_events",
        "review_tasks",
        "caseworker_actions",
        "outbox",
    ):
        assert scoped_filter(worker, collection) == {"case_id": C1}
        assert scoped_filter(worker, collection, {"seq": 3}) == {
            "$and": [{"seq": 3}, {"case_id": C1}]
        }
    with pytest.raises(Forbidden):
        scoped_filter(Scope(ScopeKind.worker, household_id=H1), "cases")


# --- Scoped insert -----------------------------------------------------------------------------


class _FakeCollection:
    def __init__(self) -> None:
        self.inserted: list[dict[str, object]] = []

    async def insert_one(self, document: dict[str, object], session: object = None) -> None:
        self.inserted.append(document)


class _FakeDb(dict[str, _FakeCollection]):
    def __missing__(self, name: str) -> _FakeCollection:
        self[name] = _FakeCollection()
        return self[name]


async def test_FR_000_19_insert_refuses_a_document_outside_the_scope() -> None:
    db = _FakeDb()
    own = {"_id": CASE, "case_id": CASE, "household_id": HOUSEHOLD}
    other = {"_id": uuid.uuid4(), "case_id": uuid.uuid4(), "household_id": uuid.uuid4()}

    await insert_one(db, "cases", scope_for(Role.applicant), own)  # type: ignore[arg-type]
    with pytest.raises(Forbidden):
        await insert_one(db, "cases", scope_for(Role.applicant), other)  # type: ignore[arg-type]
    with pytest.raises(ScopeRequired):
        await insert_one(db, "cases", None, own)  # type: ignore[arg-type]

    assert db["cases"].inserted == [own]


# --- Applications (001, ADR-001-08) ------------------------------------------------------------

H2 = uuid.uuid4()


async def test_FR_001_24_applicant_scope_cannot_read_other_household_application() -> None:
    from packages.platform.scope import SCOPED_WITH_CASE

    assert "applications" in SCOPED_WITH_CASE
    other = scope_for(Role.applicant, H2)
    own = {"_id": CASE, "case_id": CASE, "household_id": HOUSEHOLD}
    db = _FakeDb()

    assert scoped_filter(other, "applications", {"_id": CASE}) == {
        "$and": [{"_id": CASE}, {"household_id": H2}]
    }
    assert scoped_filter(scope_for(Role.caseworker, None), "applications") == {}
    assert scoped_filter(Scope.for_worker(CASE, HOUSEHOLD), "applications") == {"case_id": CASE}
    with pytest.raises(Forbidden):  # H1's application written under H2's scope
        await insert_one(db, "applications", other, own)  # type: ignore[arg-type]
    await insert_one(db, "applications", scope_for(Role.applicant), own)  # type: ignore[arg-type]
    assert db["applications"].inserted == [own]
