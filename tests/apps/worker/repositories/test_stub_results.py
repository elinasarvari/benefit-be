"""The 001 stub reader against the local replica-set MongoDB (research R1, ADR-001-06)."""

from __future__ import annotations

import uuid

import pytest

from apps.worker.repositories.stub_results import MongoStubResults
from packages.domain.case import DeterminationOutcome
from packages.platform.mongo import make_client
from packages.platform.scope import Scope
from packages.platform.settings import Settings

pytestmark = pytest.mark.integration


async def test_FR_001_16_worker_reads_only_its_households_stub_result(
    app_url: str, owner_url: str, integration_settings: Settings
) -> None:
    mine, other = uuid.uuid4(), uuid.uuid4()
    owner = make_client(owner_url)
    try:
        await owner[integration_settings.MONGO_DB]["household_stub_results"].insert_many(
            [
                {"_id": mine, "household_id": mine, "stub_result": "cannot_determine"},
                {"_id": other, "household_id": other, "stub_result": "ineligible"},
            ]
        )
    finally:
        await owner.close()

    app = make_client(app_url)
    try:
        results = MongoStubResults(app, integration_settings.MONGO_DB)
        found = await results.outcome_for(Scope.for_worker(uuid.uuid4(), mine))
        unknown = await results.outcome_for(Scope.for_worker(uuid.uuid4(), uuid.uuid4()))
    finally:
        await app.close()

    assert found is DeterminationOutcome.cannot_determine
    assert unknown is DeterminationOutcome.eligible
