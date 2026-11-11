from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from db.seeds.generate import application_documents, household_documents
from db.seeds.households import build_dataset
from db.seeds.ids import HOUSEHOLD_A, HOUSEHOLD_B, HOUSEHOLD_C, SEED, SEED_ANCHOR_DATE, seeded_id
from db.seeds.reset import ResetRefused, guard, reset
from db.seeds.users import DEMO_ACCOUNTS
from packages.platform.settings import Settings
from tests.apps.worker.case_factories import case_document
from tests.integration.fixtures import open_client, rebuild


async def test_FR_000_02_reset_restores_seeded_counts_and_markers(
    integration_settings: Settings, owner_url: str
) -> None:
    async with open_client(owner_url) as client:
        db = client[integration_settings.MONGO_DB]
        await rebuild(integration_settings)
        await db["households"].insert_one({"_id": uuid.uuid4(), "created_at": datetime.now(UTC)})
        await db["households"].delete_one({"_id": HOUSEHOLD_A})

        await reset(integration_settings, analytics=False)

        ids = {doc["_id"] async for doc in db["households"].find({}, {"_id": 1})}
        marker = await db["_meta"].find_one({"_id": "dataset"})
    assert len(ids) == 200
    assert {HOUSEHOLD_A, HOUSEHOLD_B, HOUSEHOLD_C} <= ids
    assert marker is not None
    assert marker["synthetic"] is True


def test_FR_003_14_households_carry_no_stub_result_and_demo_applicants_link_to_a_b_c() -> None:
    # Replaces the 001 stub-result test: the real engine decides now and `stub_result` and
    # `household_stub_results` are gone (ADR-003-03, ADR-003-04, T011/T012). A, B and C stay
    # three of the 200 generated households and the three applicant demo users' households.
    documents = household_documents(build_dataset(SEED_ANCHOR_DATE))

    synthetic_ids = {seeded_id(f"household-{name}") for name in ("a", "b", "c")}
    assert len(documents) == 200
    assert synthetic_ids <= {doc["_id"] for doc in documents}
    assert all(
        set(doc) == {"_id", "created_at", "address_state", "seed_key", "synthetic"}
        for doc in documents
    )
    applicant_households = {hid for *_, hid in DEMO_ACCOUNTS if hid is not None}
    assert applicant_households == synthetic_ids


async def test_FR_000_02_reset_refuses_non_local_host(integration_settings: Settings) -> None:
    remote = integration_settings.model_copy(
        update={
            "MONGO_URL_OWNER": integration_settings.MONGO_URL_OWNER.__class__(
                "mongodb://owner:pw@db.example.com:27017/?replicaSet=rs0"
            )
        }
    )

    with pytest.raises(ResetRefused, match="non-local"):
        await guard(remote)


async def test_FR_000_02_reset_refuses_unmarked_non_empty_database(
    integration_settings: Settings, owner_url: str
) -> None:
    try:
        async with open_client(owner_url) as client:
            await client[integration_settings.MONGO_DB]["_meta"].delete_many({})

        with pytest.raises(ResetRefused, match="no synthetic marker"):
            await guard(integration_settings)
    finally:
        await rebuild(integration_settings)


async def test_FR_003_10_meta_records_synthetic_seed_and_anchor_date(
    integration_settings: Settings, owner_url: str
) -> None:
    await reset(integration_settings, analytics=False)

    async with open_client(owner_url) as client:
        meta = await client[integration_settings.MONGO_DB]["_meta"].find_one({"_id": "dataset"})
    assert meta is not None
    assert meta["synthetic"] is True
    assert meta["seed"] == SEED
    assert meta["anchor_date"].date() == integration_settings.SEED_ANCHOR_DATE


async def test_FR_003_06_reset_mirrors_both_rule_sets_into_collection(
    integration_settings: Settings, owner_url: str
) -> None:
    await reset(integration_settings, analytics=False)

    async with open_client(owner_url) as client:
        docs = [d async for d in client[integration_settings.MONGO_DB]["rule_sets"].find({})]
    assert {d["_id"] for d in docs} >= {"IL-ADULT-2025@1", "IL-ADULT-2026@1"}


async def test_FR_001_24_every_seeded_case_has_a_synthetic_application(
    integration_settings: Settings, owner_url: str
) -> None:
    async with open_client(owner_url) as client:
        db = client[integration_settings.MONGO_DB]
        await rebuild(integration_settings)
        case_ids = {doc["_id"] async for doc in db["cases"].find({}, {"_id": 1})}
        applications = [doc async for doc in db["applications"].find()]

    assert {doc["_id"] for doc in applications} == case_ids
    assert all(doc["case_id"] == doc["_id"] and doc["synthetic"] is True for doc in applications)


async def test_FR_001_24_seed_gives_an_existing_case_a_synthetic_application(
    integration_settings: Settings, owner_url: str
) -> None:
    """TC-87 with a case present, so the check above cannot pass with zero rows."""
    from db.seeds.generate import seed

    case_id = uuid.uuid4()
    async with open_client(owner_url) as client:
        db = client[integration_settings.MONGO_DB]
        await rebuild(integration_settings)
        for name in (
            "households",
            "cases",  # the seeded history cases (003) would also get applications
            "seeded_verifications",
            "reference_records",
            "household_members",
            "users",
            "applications",
        ):
            await db[name].delete_many({})
        await db["cases"].insert_one(case_document(case_id, HOUSEHOLD_A))
        await seed(integration_settings)
        applications = [doc async for doc in db["applications"].find()]
        await rebuild(integration_settings)

    assert [doc["_id"] for doc in applications] == [case_id]
    assert applications[0]["synthetic"] is True


def test_FR_001_24_application_documents_match_cases_with_synthetic_names() -> None:
    case_id = uuid.uuid4()
    case_start = datetime(2026, 3, 17, 15, tzinfo=UTC)
    case = {
        "_id": case_id,
        "case_id": case_id,
        "household_id": HOUSEHOLD_A,
        "submission_reference": "sub-seed-1",
        "clock": {"case_start": case_start},
    }

    [doc] = application_documents([case])

    assert doc["_id"] == case_id == doc["case_id"]
    assert doc["household_id"] == HOUSEHOLD_A
    assert doc["submission_reference"] == "sub-seed-1"
    assert doc["synthetic"] is True
    assert doc["submitted_at"] == case_start
    assert all(m["full_name"].startswith("SYNTH-") for m in doc["form"]["members"])
    assert application_documents([]) == []
