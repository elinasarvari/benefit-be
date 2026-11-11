"""MongoDeterminationRepository against the local MongoDB (TC-114; FR-003-23, ADR-003-05)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from apps.api.repositories.determinations import MAX_DETERMINATIONS, MongoDeterminationRepository
from apps.worker.repositories.documents import to_bson
from packages.domain.auth import Principal, Role
from packages.domain.eligibility import DeterminationRecord
from packages.platform.scope import Scope
from packages.platform.settings import Settings
from packages.rules.engine import evaluate
from tests.integration.fixtures import open_client, rebuild
from tests.packages.rules.conftest import member, snapshot

NOW = datetime(2026, 11, 10, 15, 0, tzinfo=UTC)
AS_OF = date(2026, 6, 1)


def stored(value: Any) -> Any:
    """The shape migration 0005 validates: money as strings, dates as midnight-UTC BSON dates."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: stored(v) for k, v in value.items()}
    if isinstance(value, list):
        return [stored(v) for v in value]
    return to_bson(value)


def document(case_id: uuid.UUID, minutes: int) -> dict[str, Any]:
    snap = snapshot([member()])
    record = DeterminationRecord(
        determination_id=uuid.uuid4(),
        household_id=snap.household_id,
        case_id=case_id,
        snapshot_id=uuid.uuid4(),
        recorded_at=NOW + timedelta(minutes=minutes),
        determination=evaluate(snap, AS_OF),
    )
    data = record.model_dump()
    data["_id"] = data.pop("determination_id")
    return stored(data)  # type: ignore[no-any-return]


@pytest.mark.integration
async def test_FR_003_23_repository_reads_case_determinations_by_recorded_at_desc_max_20(
    integration_settings: Settings, owner_url: str, app_url: str
) -> None:
    await rebuild(integration_settings)
    case_id, other_case = uuid.uuid4(), uuid.uuid4()
    docs = [document(case_id, minutes) for minutes in range(21)] + [document(other_case, 99)]
    async with open_client(owner_url) as owner:
        await owner[integration_settings.MONGO_DB]["determinations"].insert_many(docs)
    scope = Scope.for_principal(
        Principal(
            user_id=uuid.uuid4(), role=Role.caseworker, household_id=None, session_expires_at=NOW
        )
    )

    async with open_client(app_url) as client:
        records = await MongoDeterminationRepository(
            client, integration_settings.MONGO_DB
        ).list_for_case(scope, case_id)

    assert len(records) == MAX_DETERMINATIONS == 20
    assert all(r.case_id == case_id for r in records)
    times = [r.recorded_at for r in records]
    assert times == sorted(times, reverse=True)
    newest, oldest_kept = NOW + timedelta(minutes=20), NOW + timedelta(minutes=1)
    assert (times[0], times[-1]) == (newest, oldest_kept)  # minute 0 is the one left out
    assert records[0].determination.as_of_date == AS_OF
