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
    ScopeRequired,
    find_many,
    find_one,
)
from packages.platform.settings import Settings
from tests.integration.fixtures import open_client

HOUSEHOLD_A, HOUSEHOLD_B = uuid.uuid4(), uuid.uuid4()
CASE_A, CASE_B = uuid.uuid4(), uuid.uuid4()
ROOT = Path(__file__).parents[2]


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
            [
                {"_id": CASE_A, "household_id": HOUSEHOLD_A, "case_id": CASE_A},
                {"_id": CASE_B, "household_id": HOUSEHOLD_B, "case_id": CASE_B},
            ]
        )
        yield db
        await db["cases"].delete_many({})


async def test_FR_000_06_repository_without_scope_raises(cases) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ScopeRequired):
        await find_many(cases, "cases", None)
    with pytest.raises(ScopeRequired):
        await find_one(cases, "cases", None, {"_id": CASE_A})


async def test_FR_000_19_applicant_cannot_read_other_household(cases) -> None:  # type: ignore[no-untyped-def]
    applicant_a = Scope.for_principal(principal(Role.applicant, HOUSEHOLD_A))
    caseworker = Scope.for_principal(principal(Role.caseworker, None))

    own = await find_many(cases, "cases", applicant_a)
    other = await find_one(cases, "cases", applicant_a, {"_id": CASE_B})
    everything = await find_many(cases, "cases", caseworker)

    assert [doc["_id"] for doc in own] == [CASE_A]
    assert other is None
    assert {doc["_id"] for doc in everything} == {CASE_A, CASE_B}


async def test_FR_000_19_applicant_without_a_household_is_refused(cases) -> None:  # type: ignore[no-untyped-def]
    broken = Scope.for_principal(principal(Role.applicant, None))

    with pytest.raises(Forbidden):
        await find_many(cases, "cases", broken)


async def test_FR_000_19_system_scope_limited_to_its_case(cases) -> None:  # type: ignore[no-untyped-def]
    claims = SystemCredentialClaims(
        case_id=CASE_A, workflow_id="w", step="s", iat=0, exp=1, jti="j"
    )
    system = Scope.for_system(claims)

    visible = await find_many(cases, "cases", system)
    other = await find_one(cases, "cases", system, {"_id": CASE_B})

    assert [doc["_id"] for doc in visible] == [CASE_A]
    assert other is None
    with pytest.raises(Forbidden):
        await find_many(cases, "household_members", system)


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
