"""MongoExplanationRepository against the local MongoDB (FR-004-15, FR-004-16, PL-6, BA-9)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from apps.api.repositories.explanations import (
    MAX_EXPLANATIONS,
    MAX_HISTORY,
    MongoExplanationRepository,
)
from packages.domain.auth import Principal, Role
from packages.platform.scope import Scope
from packages.platform.settings import Settings
from tests.apps.worker.case_factories import case_document
from tests.integration.api.test_determinations_repository import document as determination_document
from tests.integration.fixtures import open_client, rebuild

NOW = datetime(2026, 11, 10, 15, 0, tzinfo=UTC)


def scope_for(role: Role, household_id: uuid.UUID | None = None) -> Scope:
    return Scope.for_principal(
        Principal(
            user_id=uuid.uuid4(), role=role, household_id=household_id, session_expires_at=NOW
        )
    )


def explanation_document(
    case_id: uuid.UUID,
    household_id: uuid.UUID,
    audience: str,
    minutes: int = 0,
    determination_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    return {
        "_id": uuid.uuid4(),
        "determination_id": determination_id or uuid.uuid4(),
        "case_id": case_id,
        "household_id": household_id,
        "audience": audience,
        "result": "eligible",
        "text": f"{audience} text {minutes}",
        "citations": [],
        "built_at": NOW + timedelta(minutes=minutes),
        "synthetic": True,
    }


def case_doc(household_id: uuid.UUID, day_offset: int) -> dict[str, Any]:
    case_id = uuid.uuid4()
    doc = case_document(case_id, household_id)
    doc["open"] = False
    doc["received_on"] = doc["received_on"] + timedelta(days=day_offset)
    return doc


@pytest.mark.integration
async def test_FR_004_15_list_for_case_returns_caseworker_audience_newest_first_max_20(
    integration_settings: Settings, owner_url: str, app_url: str
) -> None:
    await rebuild(integration_settings)
    case_id, other_case, household_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    docs = [explanation_document(case_id, household_id, "caseworker", m) for m in range(21)]
    docs.append(explanation_document(case_id, household_id, "applicant", 99))
    docs.append(explanation_document(other_case, household_id, "caseworker", 99))
    async with open_client(owner_url) as owner:
        await owner[integration_settings.MONGO_DB]["decision_explanations"].insert_many(docs)

    async with open_client(app_url) as client:
        found = await MongoExplanationRepository(
            client, integration_settings.MONGO_DB
        ).list_for_case(scope_for(Role.caseworker), case_id)

    assert len(found) == MAX_EXPLANATIONS == 20
    assert {e.case_id for e in found} == {case_id}
    assert {e.audience.value for e in found} == {"caseworker"}
    times = [e.built_at for e in found]
    assert times == sorted(times, reverse=True)
    assert (times[0], times[-1]) == (NOW + timedelta(minutes=20), NOW + timedelta(minutes=1))


@pytest.mark.integration
async def test_FR_004_16_history_is_household_scoped_newest_first_with_latest_applicant_text(
    integration_settings: Settings, owner_url: str, app_url: str
) -> None:
    await rebuild(integration_settings)
    household, other_household = uuid.uuid4(), uuid.uuid4()
    old_case, new_case, undecided_case = (case_doc(household, d) for d in (0, 30, 60))
    foreign_case = case_doc(other_household, 90)
    stale = {**determination_document(new_case["_id"], 0), "household_id": household}
    latest = {**determination_document(new_case["_id"], 10), "household_id": household}
    old_determination = {**determination_document(old_case["_id"], 0), "household_id": household}
    explanations = [
        explanation_document(
            new_case["_id"], household, "applicant", 0, determination_id=stale["_id"]
        ),
        explanation_document(
            new_case["_id"], household, "applicant", 10, determination_id=latest["_id"]
        ),
        explanation_document(
            new_case["_id"], household, "caseworker", 10, determination_id=latest["_id"]
        ),
        explanation_document(
            old_case["_id"], household, "applicant", 0, determination_id=old_determination["_id"]
        ),
    ]
    async with open_client(owner_url) as owner:
        db = owner[integration_settings.MONGO_DB]
        await db["cases"].insert_many([old_case, new_case, undecided_case, foreign_case])
        await db["determinations"].insert_many([stale, latest, old_determination])
        await db["decision_explanations"].insert_many(explanations)

    async with open_client(app_url) as client:
        rows = await MongoExplanationRepository(client, integration_settings.MONGO_DB).history(
            scope_for(Role.applicant, household), household
        )

    assert [r.case.case_id for r in rows] == [
        undecided_case["_id"],
        new_case["_id"],
        old_case["_id"],
    ]
    by_case = {r.case.case_id: r.explanation for r in rows}
    assert by_case[undecided_case["_id"]] is None
    newest = by_case[new_case["_id"]]
    assert newest is not None
    assert (newest.determination_id, newest.audience.value) == (latest["_id"], "applicant")
    assert by_case[old_case["_id"]] is not None


@pytest.mark.integration
async def test_FR_004_16_history_returns_at_most_50_cases(
    integration_settings: Settings, owner_url: str, app_url: str
) -> None:
    await rebuild(integration_settings)
    household = uuid.uuid4()
    cases = [case_doc(household, d) for d in range(MAX_HISTORY + 1)]
    async with open_client(owner_url) as owner:
        await owner[integration_settings.MONGO_DB]["cases"].insert_many(cases)

    async with open_client(app_url) as client:
        rows = await MongoExplanationRepository(client, integration_settings.MONGO_DB).history(
            scope_for(Role.applicant, household), household
        )

    assert len(rows) == MAX_HISTORY == 50
    assert rows[0].case.case_id == cases[-1]["_id"]


@pytest.mark.integration
async def test_FR_004_16_applicant_scope_never_reads_another_households_history(
    integration_settings: Settings, owner_url: str, app_url: str
) -> None:
    await rebuild(integration_settings)
    household, other = uuid.uuid4(), uuid.uuid4()
    async with open_client(owner_url) as owner:
        await owner[integration_settings.MONGO_DB]["cases"].insert_one(case_doc(other, 0))

    async with open_client(app_url) as client:
        rows = await MongoExplanationRepository(client, integration_settings.MONGO_DB).history(
            scope_for(Role.applicant, household), other
        )

    assert rows == []
