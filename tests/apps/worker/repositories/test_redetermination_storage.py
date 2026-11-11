"""Redetermination writes against the local replica-set MongoDB (test-plan TC-13, TC-36)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date, timedelta

import pytest

from apps.worker.repositories.cases import CaseRepository, to_bson
from apps.worker.repositories.due_cases import DueCases, Opened
from packages.domain.case import (
    CaseChange,
    CaseState,
    CaseUpdate,
    CoveragePeriod,
    CoverageStatus,
    CoverageStatusChange,
)
from packages.platform.audit import MongoAuditWriter
from packages.platform.mongo import Client, make_client
from packages.platform.scope import Scope
from packages.platform.settings import Settings
from tests.apps.worker.case_factories import T0, case_document, event, new_ids

pytestmark = pytest.mark.integration


@pytest.fixture
async def client(app_url: str) -> AsyncIterator[Client]:
    mongo = make_client(app_url)
    try:
        yield mongo
    finally:
        await mongo.close()


def period_for(household_id: uuid.UUID, case_id: uuid.UUID, end: date) -> CoveragePeriod:
    return CoveragePeriod.model_validate(
        {
            "_id": uuid.uuid5(case_id, "coverage"),
            "household_id": household_id,
            "case_id": case_id,
            "start_date": end - timedelta(days=364),
            "end_date": end,
            "status": CoverageStatus.active,
        }
    )


async def seed(owner_url: str, settings: Settings, collection: str, document: object) -> None:
    owner = make_client(owner_url)
    try:
        await owner[settings.MONGO_DB][collection].insert_one(document)
    finally:
        await owner.close()


def renewal_change(
    case_id: uuid.UUID, household_id: uuid.UUID, prior: CoveragePeriod, *, other_household: bool
) -> CaseChange:
    ev = event(case_id, household_id, 1, from_state=CaseState.submitted, to_state=CaseState.decided)
    renewed = period_for(household_id, case_id, prior.end_date + timedelta(days=365))
    return CaseChange(
        case_id=case_id,
        household_id=household_id,
        seq=1,
        event=ev,
        case_update=CaseUpdate(state=CaseState.decided, open=False),
        coverage=renewed.model_copy(update={"start_date": prior.end_date + timedelta(days=1)}),
        prior_coverage=CoverageStatusChange(
            coverage_period_id=prior.id,
            household_id=uuid.uuid4() if other_household else household_id,
            status=CoverageStatus.renewed,
        ),
    )


def scope_of(change: CaseChange) -> Scope:
    return Scope.for_worker(change.case_id, change.household_id)


async def test_FR_007_05_renew_writes_new_period_and_prior_status_in_one_transaction(
    client: Client, owner_url: str, integration_settings: Settings
) -> None:
    case_id, household_id = new_ids()
    prior_case_id = uuid.uuid4()
    prior = period_for(household_id, prior_case_id, date(2026, 4, 30))
    await seed(owner_url, integration_settings, "cases", case_document(case_id, household_id))
    await seed(
        owner_url,
        integration_settings,
        "coverage_periods",
        to_bson({**prior.model_dump(by_alias=True), "created_at": T0, "updated_at": T0}),
    )
    db = client[integration_settings.MONGO_DB]
    audit = MongoAuditWriter(client, integration_settings.MONGO_AUDIT_DB)
    repository = CaseRepository(client, integration_settings.MONGO_DB, audit)

    # Aborts: the prior period belongs to another household, so nothing at all is written.
    with pytest.raises(Exception):  # noqa: B017, PT011
        change = renewal_change(case_id, household_id, prior, other_household=True)
        await repository.apply(change, scope_of(change))
    assert await db["coverage_periods"].count_documents({"case_id": case_id}) == 0
    assert await db["case_events"].count_documents({"case_id": case_id}) == 0
    assert (await db["coverage_periods"].find_one({"_id": prior.id}))["status"] == "active"

    change = renewal_change(case_id, household_id, prior, other_household=False)
    await repository.apply(change, scope_of(change))
    assert (await db["coverage_periods"].find_one({"_id": prior.id}))["status"] == "renewed"
    renewed = await db["coverage_periods"].find_one({"case_id": case_id})
    assert renewed is not None and renewed["status"] == "active"
    assert await db["case_events"].count_documents({"case_id": case_id}) == 1


async def test_FR_007_01_open_due_skips_household_with_open_case_until_next_day(
    client: Client, owner_url: str, integration_settings: Settings
) -> None:
    household_id = uuid.uuid4()
    first = period_for(household_id, uuid.uuid4(), date(2026, 6, 30))
    second = period_for(household_id, uuid.uuid4(), date(2026, 7, 15))
    audit = MongoAuditWriter(client, integration_settings.MONGO_AUDIT_DB)
    due = DueCases(client, integration_settings.MONGO_DB, audit)

    opened = await due.open_case(first, T0, 1)
    assert opened.opened is Opened.created and opened.start is not None
    again = await due.open_case(first, T0, 1)
    assert again.opened is Opened.existing  # same period, same case: started again, not duplicated
    skipped = await due.open_case(second, T0, 1)
    assert skipped.opened is Opened.skipped and skipped.start is None

    db = client[integration_settings.MONGO_DB]
    await db["cases"].update_one(
        {"_id": opened.start.case_id, "household_id": household_id},
        {"$set": {"state": "decided", "open": False}},
    )
    next_day = await due.open_case(second, T0 + timedelta(days=1), 1)
    assert next_day.opened is Opened.created

    audit_db = client[integration_settings.MONGO_AUDIT_DB]
    entries = await audit_db["audit_log"].count_documents(
        {
            "action": "redetermination_started_by_schedule",
            "target_id": str(opened.start.case_id),
        }
    )
    assert entries == 1
    assert (
        await audit_db["audit_log"].count_documents(
            {"target_id": str(next_day.start.case_id if next_day.start else "")}
        )
        == 1
    )
