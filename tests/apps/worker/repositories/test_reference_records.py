"""The reference lookup over the seeded `reference_records` (test-plan TC-08, TC-09; ADR-007-05)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest

from apps.worker.repositories.reference_records import MongoReferenceRecords
from packages.domain.verification import IdentityMatch, ReferenceLookup
from packages.platform.mongo import Client, make_client
from packages.platform.scope import Scope
from packages.platform.settings import Settings

pytestmark = pytest.mark.integration

BORN = date(1980, 5, 17)


async def seed_record(
    owner_url: str, settings: Settings, household_id: uuid.UUID, member_id: uuid.UUID
) -> None:
    owner = make_client(owner_url)
    try:
        await owner[settings.MONGO_DB]["reference_records"].insert_one(
            {
                "_id": uuid.uuid4(),
                "household_id": household_id,
                "member_id": member_id,
                "given_name": "Marisol",
                "family_name": "Quintero",
                "date_of_birth": datetime(BORN.year, BORN.month, BORN.day, tzinfo=UTC),
                "jobs": [{"employer": "Synthetic Diner", "monthly_gross": "1200.00"}],
                "synthetic": True,
            }
        )
    finally:
        await owner.close()


def lookup_for(household_id: uuid.UUID, member_id: uuid.UUID, given: str, family: str, born: date):
    return ReferenceLookup(
        household_id=household_id,
        member_id=member_id,
        given_name=given,
        family_name=family,
        date_of_birth=born,
    )


async def test_FR_007_13_lookup_matches_case_folded_name_and_birth_date(
    app_client: Client, owner_url: str, integration_settings: Settings
) -> None:
    household_id, member_id = uuid.uuid4(), uuid.uuid4()
    await seed_record(owner_url, integration_settings, household_id, member_id)
    records = MongoReferenceRecords(app_client, integration_settings.MONGO_DB)
    scope = Scope.for_worker(uuid.uuid4(), household_id)

    same = await records.lookup(
        scope, lookup_for(household_id, member_id, "MARISOL", "quintero", BORN)
    )
    other_birth = await records.lookup(
        scope, lookup_for(household_id, member_id, "Marisol", "Quintero", date(1980, 5, 18))
    )

    assert same.identity is IdentityMatch.match
    assert [str(job.monthly_gross) for job in same.jobs] == ["1200.00"]
    assert other_birth.identity is IdentityMatch.no_match


async def test_FR_007_13_lookup_without_record_returns_no_record(
    app_client: Client, owner_url: str, integration_settings: Settings
) -> None:
    household_id, member_id, other_household = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await seed_record(owner_url, integration_settings, other_household, member_id)
    records = MongoReferenceRecords(app_client, integration_settings.MONGO_DB)
    scope = Scope.for_worker(uuid.uuid4(), household_id)

    answer = await records.lookup(
        scope, lookup_for(household_id, member_id, "Marisol", "Quintero", BORN)
    )

    assert answer.identity is IdentityMatch.no_record
    assert answer.jobs == []
