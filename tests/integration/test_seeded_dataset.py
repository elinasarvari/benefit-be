"""Seeded data is usable by every consumer lane (FR-003-13, FR-003-14, FR-003-22, SC-003-04).

The test sets are written by `db/seeds/test_sets.py` into `db/seeds/generated/testsets/`.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from db.seeds.generate import GENERATED_DIR
from db.seeds.households import YEARS, build_dataset
from db.seeds.ids import HOUSEHOLD_A, HOUSEHOLD_B, HOUSEHOLD_C, HOUSEHOLD_D
from packages.domain.eligibility import RuleId, RuleOutcome
from packages.domain.household import (
    HouseholdSnapshot,
    JobIncome,
    MemberFacts,
    PayStubFact,
)
from packages.platform.settings import Settings
from packages.rules import evaluate
from tests.integration.fixtures import open_client, rebuild

TESTSETS = GENERATED_DIR / "testsets"
ITEM_FIELDS: dict[str, set[str]] = {
    "known_answers": {"case_key", "as_of_date", "snapshot", "expected_result", "expected_failed"},
    "pay_stub_extractions": {"document_id", "path", "poor_quality", "expected"},
    "redeterminations_due": {
        "household_id",
        "coverage_period_id",
        "coverage_end",
        "redetermination_start",
        "expected_path",
    },
    "expected_metrics": {"metric", "period", "expected_value", "numerator", "denominator"},
}
SEEDED_COLLECTIONS = [
    "users",
    "households",
    "household_members",
    "household_snapshots",
    "determinations",
    "rule_sets",
    "seeded_verifications",
    "cases",
    "case_events",
    "coverage_periods",
    "outbox",
    "review_tasks",
    "caseworker_actions",
    "applications",
    "_meta",
]
DEMO = [HOUSEHOLD_A, HOUSEHOLD_B, HOUSEHOLD_C, HOUSEHOLD_D]


def _load(name: str) -> dict[str, Any]:
    path: Path = TESTSETS / f"{name}.json"
    assert path.is_file(), f"{path} was not generated"
    loaded: dict[str, Any] = json.loads(path.read_text())
    return loaded


@pytest.mark.parametrize("name", sorted(ITEM_FIELDS))
async def test_FR_003_13_consumer_lanes_load_testsets_with_domain_models(
    name: str, integration_settings: Settings, owner_url: str
) -> None:
    await rebuild(integration_settings)
    async with open_client(owner_url) as client:
        meta = await client[integration_settings.MONGO_DB]["_meta"].find_one({"_id": "dataset"})
    assert meta is not None

    testset = _load(name)

    assert testset["synthetic"] is True
    assert testset["seed"] == meta["seed"]
    assert date.fromisoformat(testset["anchor_date"]) == meta["anchor_date"].date()
    assert testset["generator_version"] == meta["generator_version"]
    assert testset["items"]
    for item in testset["items"]:
        assert ITEM_FIELDS[name] <= item.keys()
        if name == "known_answers":
            HouseholdSnapshot.model_validate(item["snapshot"])
            date.fromisoformat(item["as_of_date"])


async def test_FR_003_22_every_seeded_row_passes_its_owning_collection_validator(
    integration_settings: Settings, owner_url: str
) -> None:
    await rebuild(integration_settings)

    async with open_client(owner_url) as client:
        db = client[integration_settings.MONGO_DB]
        invalid: dict[str, int] = {}
        checked = 0
        async for info in await db.list_collections():
            validator = info.get("options", {}).get("validator", {}).get("$jsonSchema")
            if validator is None:
                continue
            checked += 1
            count = await db[info["name"]].count_documents({"$nor": [{"$jsonSchema": validator}]})
            if count:
                invalid[info["name"]] = count

    assert checked > 0
    assert not invalid, invalid


def _facts_from_documents(
    members: list[dict[str, Any]], verifications: dict[UUID, dict[str, Any]]
) -> list[MemberFacts]:
    """The engine's view as the worker's snapshot step builds it: members plus verifications."""
    facts = []
    for member in members:
        verified = verifications[member["_id"]]
        stubs = {job["job_id"]: job["stubs"] for job in verified["jobs"]}
        facts.append(
            MemberFacts(
                member_id=member["_id"],
                relationship=member["relationship"],
                date_of_birth=member["date_of_birth"].date(),
                address_state=member["address_state"],
                citizenship_attested=member["citizenship_attested"],
                medicare_entitled=member["medicare_entitled"],
                identity_verified=verified["identity_verified"],
                declared_zero_income=member["declared_zero_income"],
                jobs=[
                    JobIncome(
                        job_id=job["job_id"],
                        stubs=[
                            PayStubFact(
                                document_id=stub["document_id"],
                                pay_date=stub["pay_date"].date(),
                                frequency=stub["frequency"],
                                gross_pay=stub["gross_pay"],
                            )
                            for stub in stubs[job["job_id"]]
                        ],
                    )
                    for job in member["jobs"]
                ],
            )
        )
    return facts


async def test_FR_003_14_demo_households_evaluate_eligible_ineligible_income_cannot_determine(
    integration_settings: Settings, owner_url: str
) -> None:
    """TC-75: seeded members and seeded verifications through `evaluate` at the anchor date."""
    anchor = integration_settings.SEED_ANCHOR_DATE
    await rebuild(integration_settings)
    results = {}
    async with open_client(owner_url) as client:
        db = client[integration_settings.MONGO_DB]
        for household_id in DEMO:
            members = [
                m
                async for m in db["household_members"].find(
                    {"household_id": household_id, "coverage_year": anchor.year}
                )
            ]
            verifications = {
                v["member_id"]: v
                async for v in db["seeded_verifications"].find({"household_id": household_id})
            }
            snapshot = HouseholdSnapshot(
                snapshot_id=household_id,
                household_id=household_id,
                case_id=household_id,
                as_of_date=anchor,
                captured_at=datetime(anchor.year, anchor.month, anchor.day),
                members=_facts_from_documents(members, verifications),
            )
            results[household_id] = evaluate(snapshot, anchor)

    eligible, ineligible, undecided, plain = (results[h] for h in DEMO)
    assert plain.result.value == "eligible"  # D: the clean eligible household (applicant4)
    assert eligible.result.value == "eligible"
    assert ineligible.result.value == "ineligible"
    income = next(r for r in ineligible.rule_results if r.rule_id is RuleId.ELIG_06)
    assert income.outcome is RuleOutcome.fail
    assert ineligible.household_monthly_income is not None
    assert ineligible.monthly_income_limit is not None
    assert ineligible.household_monthly_income > ineligible.monthly_income_limit
    assert undecided.result.value == "cannot_determine"
    assert [(i.item, i.job_id is not None) for i in undecided.missing_items] == [("pay_stub", True)]


async def test_FR_003_14_demo_households_have_no_open_case_or_current_coverage(
    integration_settings: Settings, owner_url: str
) -> None:
    """TC-76: A, B and C start clean, so the demo journeys can submit (ADR-003-06)."""
    await rebuild(integration_settings)
    async with open_client(owner_url) as client:
        db = client[integration_settings.MONGO_DB]
        counts = {
            name: await db[name].count_documents({"household_id": {"$in": DEMO}})
            for name in (
                "cases",
                "case_events",
                "coverage_periods",
                "household_snapshots",
                "determinations",
                "outbox",
                "review_tasks",
                "caseworker_actions",
            )
        }
        open_cases = await db["cases"].count_documents({"open": True})
        history = await db["cases"].count_documents({})

    assert counts == dict.fromkeys(counts, 0)
    assert open_cases == 0
    assert history > 0  # the other households do have history


async def test_FR_003_16_every_seeded_member_has_one_verification_with_identity_and_stub_facts(
    integration_settings: Settings, owner_url: str
) -> None:
    """TC-77: `seeded_verifications` vs the seeded members and jobs; C's job has no stub."""
    await rebuild(integration_settings)
    dataset = build_dataset(integration_settings.SEED_ANCHOR_DATE)
    stub_ids: dict[UUID, set[UUID]] = {}
    for stub in dataset.stubs():
        stub_ids.setdefault(stub.job_id, set()).add(stub.document_id)
    async with open_client(owner_url) as client:
        db = client[integration_settings.MONGO_DB]
        members = {m["_id"]: m async for m in db["household_members"].find()}
        verifications = [v async for v in db["seeded_verifications"].find()]

    assert (
        len(verifications)
        == len(members)
        == sum(len(h.years[y].members) for h in dataset.households for y in YEARS)
    )
    assert {v["member_id"] for v in verifications} == set(members)
    for verification in verifications:
        member = members[verification["member_id"]]
        assert verification["household_id"] == member["household_id"]
        assert verification["identity_verified"] is True
        assert [j["job_id"] for j in verification["jobs"]] == [j["job_id"] for j in member["jobs"]]
        for job in verification["jobs"]:
            assert {s["document_id"] for s in job["stubs"]} == stub_ids.get(job["job_id"], set())
    household_c = [
        v
        for v in verifications
        if v["household_id"] == HOUSEHOLD_C
        and members[v["member_id"]]["coverage_year"] == integration_settings.SEED_ANCHOR_DATE.year
    ]
    assert any(job["stubs"] == [] for v in household_c for job in v["jobs"])


async def test_FR_003_12_every_seeded_record_in_every_collection_is_marked_synthetic(
    integration_settings: Settings, owner_url: str
) -> None:
    """TC-85: every seeded document carries `synthetic: true`, in every seeded collection."""
    await rebuild(integration_settings)
    async with open_client(owner_url) as client:
        db = client[integration_settings.MONGO_DB]
        total = {name: await db[name].count_documents({}) for name in SEEDED_COLLECTIONS}
        unmarked = {
            name: await db[name].count_documents({"synthetic": {"$ne": True}})
            for name in SEEDED_COLLECTIONS
        }

    assert all(count > 0 for count in total.values()), total
    assert unmarked == dict.fromkeys(SEEDED_COLLECTIONS, 0)


async def test_FR_003_22_collection_counts_match_manifest_after_reset(
    integration_settings: Settings, owner_url: str
) -> None:
    """TC-83: the manifest's document counts equal what the database holds."""
    await rebuild(integration_settings)
    manifest = json.loads((GENERATED_DIR / "manifest.json").read_text())
    async with open_client(owner_url) as client:
        db = client[integration_settings.MONGO_DB]
        actual = {name: await db[name].count_documents({}) for name in manifest["collections"]}

    assert set(manifest["collections"]) >= set(SEEDED_COLLECTIONS) - {"_meta"}
    assert actual == manifest["collections"]
