"""Worker stores for verified facts, snapshots and determinations against MongoDB (T040).

TC-86 (verified facts scoped to the household), TC-87 (snapshot insert idempotent), TC-88 (a
duplicate determination counts as done, TW-11). Synthetic data only (PR-3).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

from apps.worker.repositories.determinations import (
    DETERMINATIONS,
    MongoDeterminationStore,
)
from apps.worker.repositories.snapshots import HOUSEHOLD_SNAPSHOTS, MongoSnapshotSource
from apps.worker.repositories.verified_facts import SEEDED_VERIFICATIONS, SeededVerifications
from packages.domain.eligibility import DeterminationRecord
from packages.platform.mongo import Client, make_client
from packages.platform.scope import Scope
from packages.platform.settings import Settings
from packages.rules.engine import evaluate
from tests.apps.worker.eligibility_fakes import (
    AS_OF,
    NOW,
    FakeSource,
    FakeVerified,
    capture,
    stored_member,
    verified,
)

pytestmark = pytest.mark.integration

H1 = uuid.UUID("00000000-0000-4000-8000-0000000003d1")
H2 = uuid.UUID("00000000-0000-4000-8000-0000000003d2")
MEMBER_1 = uuid.UUID("00000000-0000-4000-8000-000000000d11")
MEMBER_2 = uuid.UUID("00000000-0000-4000-8000-000000000d22")


@pytest.fixture
async def app(app_url: str) -> AsyncIterator[Client]:
    client = make_client(app_url)
    try:
        yield client
    finally:
        await client.close()


@pytest.fixture
async def owner(owner_url: str) -> AsyncIterator[Client]:
    client = make_client(owner_url)
    try:
        yield client
    finally:
        await client.close()


def verification(household_id: uuid.UUID, member_id: uuid.UUID) -> dict[str, Any]:
    return {
        "_id": uuid.uuid4(),
        "household_id": household_id,
        "member_id": member_id,
        "identity_verified": True,
        "jobs": [],
        "synthetic": True,
    }


async def test_FR_003_16_seeded_verified_facts_returns_only_the_scoped_households_members(
    app: Client, owner: Client, integration_settings: Settings
) -> None:
    db = owner[integration_settings.MONGO_DB]
    await db[SEEDED_VERIFICATIONS].delete_many({"household_id": {"$in": [H1, H2]}})
    await db[SEEDED_VERIFICATIONS].insert_many(
        [verification(H1, MEMBER_1), verification(H2, MEMBER_2)]
    )
    facts = SeededVerifications(app, integration_settings.MONGO_DB)

    first = await facts.for_household(Scope.for_worker(uuid.uuid4(), H1))
    second = await facts.for_household(Scope.for_worker(uuid.uuid4(), H2))

    assert [f.member_id for f in first] == [MEMBER_1]
    assert [f.household_id for f in first] == [H1]
    assert [f.member_id for f in second] == [MEMBER_2]


async def _snapshot_for(case_id: uuid.UUID, household_id: uuid.UUID) -> Any:
    source = FakeSource(members=[stored_member(household_id=household_id)])
    scope = Scope.for_worker(case_id, household_id)
    return await capture(source, FakeVerified([verified()])).capture(scope, AS_OF)


async def test_FR_003_19_snapshot_insert_twice_keeps_one_document(
    app: Client, owner: Client, integration_settings: Settings
) -> None:
    case_id = uuid.uuid4()
    scope = Scope.for_worker(case_id, H1)
    snapshot = await _snapshot_for(case_id, H1)
    source = MongoSnapshotSource(app, integration_settings.MONGO_DB)

    first = await source.insert_snapshot(scope, snapshot)
    second = await source.insert_snapshot(scope, snapshot)

    assert (first, second) == (True, False)
    stored = await owner[integration_settings.MONGO_DB][HOUSEHOLD_SNAPSHOTS].count_documents(
        {"case_id": case_id}
    )
    assert stored == 1
    assert await source.snapshot_for(scope, AS_OF) == snapshot  # round trip through BSON


async def test_FR_003_04_determination_insert_duplicate_key_is_treated_as_done(
    app: Client, owner: Client, integration_settings: Settings
) -> None:
    case_id = uuid.uuid4()
    scope = Scope.for_worker(case_id, H1)
    snapshot = await _snapshot_for(case_id, H1)
    record = DeterminationRecord(
        determination_id=uuid.uuid5(case_id, f"determination:{AS_OF.isoformat()}"),
        household_id=H1,
        case_id=case_id,
        snapshot_id=snapshot.snapshot_id,
        recorded_at=NOW,
        determination=evaluate(snapshot, AS_OF),
    )
    store = MongoDeterminationStore(app, integration_settings.MONGO_DB)

    first = await store.insert(scope, record)
    second = await store.insert(scope, record)  # no error

    assert (first, second) == (True, False)
    count = await owner[integration_settings.MONGO_DB][DETERMINATIONS].count_documents(
        {"case_id": case_id}
    )
    assert count == 1
