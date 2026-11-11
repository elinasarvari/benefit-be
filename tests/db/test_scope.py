from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from packages.domain.auth import Principal, Role, SystemCredentialClaims
from packages.platform.errors import Forbidden
from packages.platform.scope import (
    HOUSEHOLD_SCOPED,
    Scope,
    ScopeKind,
    ScopeRequired,
    find_many,
    find_one,
)
from packages.platform.settings import Settings
from tests.integration.fixtures import open_client

HOUSEHOLD_A, HOUSEHOLD_B = uuid.uuid4(), uuid.uuid4()
CASE_A, CASE_B = uuid.uuid4(), uuid.uuid4()
ROOT = Path(__file__).parents[2]
PROJECTION = {"_id": 1, "case_id": 1, "household_id": 1}


def case_document(case_id: uuid.UUID, household_id: uuid.UUID) -> dict[str, object]:
    """A case that passes the 0003_case_lifecycle validator."""
    now = datetime(2026, 3, 17, 15, tzinfo=UTC)
    return {
        "_id": case_id,
        "household_id": household_id,
        "case_id": case_id,
        "case_type": "application",
        "submission_reference": f"sub-{case_id}",
        "state": "submitted",
        "open": True,
        "received_on": datetime(2026, 3, 17, tzinfo=UTC),
        "decision_deadline": datetime(2026, 5, 1, tzinfo=UTC),
        "clock": {"real_start": now, "case_start": now, "scale": 1, "offset_seconds": 0},
        "last_seq": 0,
        "created_at": now,
        "updated_at": now,
    }


def principal(role: Role, household: uuid.UUID | None) -> Principal:
    return Principal(
        user_id=uuid.uuid4(),
        role=role,
        household_id=household,
        session_expires_at=datetime.now(UTC),
    )


@pytest.fixture
async def cases(integration_settings: Settings, owner_url: str):  # type: ignore[no-untyped-def]
    async with open_client(owner_url) as client:
        db = client[integration_settings.MONGO_DB]
        await db["cases"].delete_many({})
        await db["cases"].insert_many(
            [case_document(CASE_A, HOUSEHOLD_A), case_document(CASE_B, HOUSEHOLD_B)]
        )
        yield db
        await db["cases"].delete_many({})


async def test_FR_000_06_repository_without_scope_raises(cases) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ScopeRequired):
        await find_many(cases, "cases", None, projection=PROJECTION)
    with pytest.raises(ScopeRequired):
        await find_one(cases, "cases", None, {"_id": CASE_A}, projection=PROJECTION)


async def test_FR_000_19_applicant_cannot_read_other_household(cases) -> None:  # type: ignore[no-untyped-def]
    applicant_a = Scope.for_principal(principal(Role.applicant, HOUSEHOLD_A))
    caseworker = Scope.for_principal(principal(Role.caseworker, None))

    own = await find_many(cases, "cases", applicant_a, projection=PROJECTION)
    other = await find_one(cases, "cases", applicant_a, {"_id": CASE_B}, projection=PROJECTION)
    everything = await find_many(cases, "cases", caseworker, projection=PROJECTION)

    assert [doc["_id"] for doc in own] == [CASE_A]
    assert other is None
    assert {doc["_id"] for doc in everything} == {CASE_A, CASE_B}


async def test_FR_000_19_applicant_without_a_household_is_refused(cases) -> None:  # type: ignore[no-untyped-def]
    broken = Scope.for_principal(principal(Role.applicant, None))

    with pytest.raises(Forbidden):
        await find_many(cases, "cases", broken, projection=PROJECTION)


async def test_FR_000_19_system_scope_limited_to_its_case(cases) -> None:  # type: ignore[no-untyped-def]
    claims = SystemCredentialClaims(
        case_id=CASE_A, workflow_id="w", step="s", iat=0, exp=1, jti="j"
    )
    system = Scope.for_system(claims)

    visible = await find_many(cases, "cases", system, projection=PROJECTION)
    other = await find_one(cases, "cases", system, {"_id": CASE_B}, projection=PROJECTION)

    assert [doc["_id"] for doc in visible] == [CASE_A]
    assert other is None
    with pytest.raises(Forbidden):
        await find_many(cases, "household_members", system, projection=PROJECTION)


async def test_FR_003_16_worker_scope_cannot_read_another_households_seeded_verifications(
    integration_settings: Settings, owner_url: str
) -> None:
    member_a, member_b = uuid.uuid4(), uuid.uuid4()
    async with open_client(owner_url) as client:
        db = client[integration_settings.MONGO_DB]
        await db["seeded_verifications"].delete_many({})
        await db["seeded_verifications"].insert_many(
            [
                {
                    "_id": uuid.uuid4(),
                    "household_id": household,
                    "member_id": member,
                    "identity_verified": True,
                    "jobs": [],
                }
                for household, member in ((HOUSEHOLD_A, member_a), (HOUSEHOLD_B, member_b))
            ]
        )
        worker_a = Scope.for_worker(CASE_A, HOUSEHOLD_A)
        projection = {"_id": 0, "household_id": 1, "member_id": 1}

        own = await find_many(db, "seeded_verifications", worker_a, projection=projection)
        other = await find_one(
            db, "seeded_verifications", worker_a, {"member_id": member_b}, projection=projection
        )
        await db["seeded_verifications"].delete_many({})

    assert own == [{"household_id": HOUSEHOLD_A, "member_id": member_a}]
    assert other is None
    with pytest.raises(Forbidden):
        await find_many(
            db,
            "seeded_verifications",
            Scope(ScopeKind.worker, case_id=CASE_A),
            projection=projection,
        )


def test_FR_000_19_no_unscoped_queries_on_household_collections() -> None:
    names = "|".join(sorted(HOUSEHOLD_SCOPED))
    direct = re.compile(
        rf"""(\[\s*["'](?:{names})["']\s*\]|\.(?:{names})\.(?:find|aggregate|update|delete|insert)
        |get_collection\(\s*["'](?:{names})["'])""",
        re.VERBOSE,
    )
    allowed = ROOT / "packages" / "platform" / "scope.py"
    offenders = [
        f"{path.relative_to(ROOT)}: {match.group(0)}"
        for folder in ("apps", "packages")
        for path in (ROOT / folder).rglob("*.py")
        if path != allowed
        for match in direct.finditer(path.read_text())
    ]

    assert offenders == []
