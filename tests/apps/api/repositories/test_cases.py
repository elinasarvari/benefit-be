"""apps/api/repositories/cases.py: storage mapping, then keyset lists and the applications
insert against the local MongoDB (T018, T060; TC-92, TC-93, TC-94)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from pymongo.errors import DuplicateKeyError

from apps.api.repositories.cases import (
    HouseholdHasOpenCase,
    MongoCaseRepository,
    _case_from_bson,
    _is_open_case_guard,
    bson_date,
    to_bson,
)
from packages.domain.auth import Principal, Role
from packages.domain.case import (
    Case,
    CaseClock,
    CaseState,
    CaseType,
    StoredApplication,
)
from packages.platform.mongo import Client
from packages.platform.scope import Scope
from packages.platform.settings import Settings
from tests.apps.api.forms import form
from tests.integration.fixtures import open_client, rebuild


def test_FR_001_23_calendar_dates_stored_as_midnight_utc_and_read_back() -> None:
    doc = to_bson({"received_on": date(2026, 3, 17), "state": CaseState.submitted})

    assert doc == {"received_on": datetime(2026, 3, 17, tzinfo=UTC), "state": "submitted"}
    assert bson_date(date(2026, 3, 17)) == doc["received_on"]


def test_FR_001_23_partial_index_violation_maps_to_open_case_guard() -> None:
    open_case = DuplicateKeyError("dup", 11000, {"keyPattern": {"household_id": 1}})
    same_case = DuplicateKeyError("dup", 11000, {"keyPattern": {"_id": 1}})

    assert _is_open_case_guard(open_case) is True
    assert _is_open_case_guard(same_case) is False


def test_FR_001_11_stored_case_round_trips_to_the_domain_model() -> None:
    case_id = "6f1c2a54-0b7e-4d3a-9c41-2e8f0d5b7a13"
    instant = datetime(2026, 3, 17, 15, tzinfo=UTC)
    stored = {
        "_id": case_id,
        "case_id": case_id,
        "household_id": case_id,
        "case_type": "application",
        "submission_reference": "sub-A",
        "state": "submitted",
        "open": True,
        "received_on": datetime(2026, 3, 17, tzinfo=UTC),
        "decision_deadline": datetime(2026, 5, 1, tzinfo=UTC),
        "clock": {"real_start": instant, "case_start": instant, "scale": 1, "offset_seconds": 0},
        "created_at": instant,
        "updated_at": instant,
    }

    case = _case_from_bson(stored)

    assert (case.received_on, case.decision_deadline) == (date(2026, 3, 17), date(2026, 5, 1))


# --- Local MongoDB -----------------------------------------------------------------------------

NOW = datetime(2026, 3, 17, 15, 0, tzinfo=UTC)


def applicant(household: uuid.UUID) -> Scope:
    return Scope.for_principal(
        Principal(
            user_id=uuid.uuid4(),
            role=Role.applicant,
            household_id=household,
            session_expires_at=NOW,
        )
    )


def staff_scope() -> Scope:
    return Scope.for_principal(
        Principal(
            user_id=uuid.uuid4(), role=Role.caseworker, household_id=None, session_expires_at=NOW
        )
    )


def new_case(
    household: uuid.UUID,
    *,
    deadline: date,
    state: CaseState = CaseState.submitted,
    open_: bool = True,
    case_id: uuid.UUID | None = None,
) -> Case:
    cid = case_id or uuid.uuid4()
    clock = CaseClock(real_start=NOW, case_start=NOW, scale=1)
    return Case(
        id=cid,
        case_id=cid,
        household_id=household,
        case_type=CaseType.application,
        submission_reference=str(uuid.uuid4()),
        state=state,
        open=open_,
        received_on=deadline - timedelta(days=45),
        decision_deadline=deadline,
        clock=clock,
        created_at=NOW,
        updated_at=NOW,
    )


async def put(client: Client, settings: Settings, cases: list[Case]) -> None:
    await client[settings.MONGO_DB]["cases"].insert_many(
        [to_bson(c.model_dump(by_alias=True)) for c in cases]
    )


async def fresh(settings: Settings) -> None:
    """Seeded stores minus the cases: these tests count and page their own cases only, and the
    003 history seed (FR-003-22, ADR-003-06) adds decided cases with their applications."""
    await rebuild(settings)
    async with open_client(settings.MONGO_URL_OWNER.get_secret_value()) as owner:
        for name in ("cases", "applications"):
            await owner[settings.MONGO_DB][name].delete_many({})


@pytest.mark.integration
async def test_FR_001_27_case_list_keyset_pages_by_deadline_then_id(
    integration_settings: Settings, owner_url: str, app_url: str
) -> None:
    await fresh(integration_settings)
    base = date(2026, 5, 1)
    # 120 open cases, 10 of them sharing one deadline; every one in its own household.
    cases = [
        new_case(uuid.uuid4(), deadline=base + timedelta(days=0 if n < 10 else n))
        for n in range(120)
    ]
    async with open_client(owner_url) as owner:
        await put(owner, integration_settings, cases)
    expected = [c.id for c in sorted(cases, key=lambda c: (c.decision_deadline, c.id))]

    async with open_client(app_url) as client:
        repo = MongoCaseRepository(client, integration_settings.MONGO_DB)
        seen: list[uuid.UUID] = []
        after = None
        sizes = []
        while True:
            rows = await repo.list_cases(staff_scope(), state=None, after=after, limit=50 + 1)
            page = rows[:50]
            sizes.append(len(page))
            seen += [c.id for c in page]
            if len(rows) <= 50:
                break
            after = (page[-1].decision_deadline, page[-1].id)

    assert sizes == [50, 50, 20]
    assert seen == expected  # no gap, no overlap, stable inside the shared deadline


@pytest.mark.integration
async def test_FR_001_27_case_list_filters_by_state(
    integration_settings: Settings, owner_url: str, app_url: str
) -> None:
    await fresh(integration_settings)
    day = date(2026, 5, 1)
    submitted = new_case(uuid.uuid4(), deadline=day)
    review_a = new_case(uuid.uuid4(), deadline=day, state=CaseState.under_review)
    review_b = new_case(
        uuid.uuid4(), deadline=day + timedelta(days=1), state=CaseState.under_review
    )
    decided = new_case(uuid.uuid4(), deadline=day, state=CaseState.decided, open_=False)
    async with open_client(owner_url) as owner:
        await put(owner, integration_settings, [submitted, review_a, review_b, decided])

    async with open_client(app_url) as client:
        repo = MongoCaseRepository(client, integration_settings.MONGO_DB)
        reviewing = await repo.list_cases(
            staff_scope(), state=CaseState.under_review, after=None, limit=10
        )
        default = await repo.list_cases(staff_scope(), state=None, after=None, limit=10)
        closed = await repo.list_cases(staff_scope(), state=CaseState.decided, after=None, limit=10)

    assert {c.id for c in reviewing} == {review_a.id, review_b.id}
    assert {c.id for c in default} == {submitted.id, review_a.id, review_b.id}  # open only
    assert [c.id for c in closed] == [decided.id]


@pytest.mark.integration
async def test_FR_001_26_applicant_list_holds_only_own_household_open_cases(
    integration_settings: Settings, owner_url: str, app_url: str
) -> None:
    await fresh(integration_settings)
    h1, h2 = uuid.uuid4(), uuid.uuid4()
    day = date(2026, 5, 1)
    own_open = new_case(h1, deadline=day)
    own_decided = new_case(h1, deadline=day, state=CaseState.decided, open_=False)
    other = new_case(h2, deadline=day)
    async with open_client(owner_url) as owner:
        await put(owner, integration_settings, [own_open, own_decided, other])

    async with open_client(app_url) as client:
        repo = MongoCaseRepository(client, integration_settings.MONGO_DB)
        mine = await repo.list_cases(applicant(h1), state=None, after=None, limit=10)
        theirs = await repo.list_cases(applicant(h2), state=None, after=None, limit=10)
        found_other = await repo.find_case(applicant(h1), other.id)
        open_id = await repo.find_open_case_id(applicant(h1), h1)

    assert [c.id for c in mine] == [own_open.id]
    assert [c.id for c in theirs] == [other.id]
    assert found_other is None
    assert open_id == own_open.id


@pytest.mark.integration
async def test_FR_001_24_application_is_written_with_the_case_in_one_transaction(
    integration_settings: Settings, app_url: str
) -> None:
    await fresh(integration_settings)
    household = uuid.uuid4()
    scope = applicant(household)
    case = new_case(household, deadline=date(2026, 5, 1))
    stored = StoredApplication(
        id=case.id,
        case_id=case.id,
        household_id=household,
        submission_reference=case.submission_reference,
        form=form(),
        submitted_at=NOW,
        synthetic=True,
    )
    clash = new_case(household, deadline=date(2026, 5, 2))  # second open case, refused

    async with open_client(app_url) as client:
        repo = MongoCaseRepository(client, integration_settings.MONGO_DB)

        async def open_it(tx):  # type: ignore[no-untyped-def]
            await tx.insert_case(scope, case)
            await tx.insert_application(scope, stored)

        async def open_clash(tx):  # type: ignore[no-untyped-def]
            await tx.insert_application(
                scope, stored.model_copy(update={"id": clash.id, "case_id": clash.id})
            )
            await tx.insert_case(scope, clash)

        await repo.run_in_transaction(open_it)
        try:
            await repo.run_in_transaction(open_clash)
        except HouseholdHasOpenCase:
            pass
        else:  # pragma: no cover
            raise AssertionError("the open-case guard did not fire")
        db = client[integration_settings.MONGO_DB]
        count = await db["applications"].count_documents({})
        doc = await db["applications"].find_one({"_id": case.id})

    assert count == 1  # the refused submission's application rolled back with its case
    assert doc is not None
    assert doc["form"]["members"][0]["jobs"][0]["declared_monthly_gross"].to_decimal() == 1800
    assert doc["form"]["members"][0]["date_of_birth"] == datetime(1985, 4, 12, tzinfo=UTC)
