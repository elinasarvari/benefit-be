"""record_case_change storage against the local replica-set MongoDB (ADR-001-01; TC-18–TC-23)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from pymongo.asynchronous.client_session import AsyncClientSession

from apps.worker.repositories.cases import CaseRepository
from packages.domain.case import CaseChange, CaseState, ReviewTaskStatus
from packages.platform.audit import MongoAuditWriter
from packages.platform.mongo import Client, make_client
from packages.platform.scope import Scope
from packages.platform.settings import Settings
from tests.apps.worker.case_factories import (
    DETERMINATION_ID,
    REVIEWER,
    SYNTH_REASON,
    approved,
    case_document,
    move,
    new_ids,
)

pytestmark = pytest.mark.integration

S = CaseState


@pytest.fixture
async def client(app_url: str) -> AsyncIterator[Client]:
    mongo = make_client(app_url)
    try:
        yield mongo
    finally:
        await mongo.close()


@pytest.fixture
def repository(client: Client, integration_settings: Settings) -> CaseRepository:
    audit = MongoAuditWriter(client, integration_settings.MONGO_AUDIT_DB)
    return CaseRepository(client, integration_settings.MONGO_DB, audit)


@pytest.fixture
async def case_ids(owner_url: str, integration_settings: Settings) -> tuple[uuid.UUID, uuid.UUID]:
    """A fresh submitted case, inserted as the API would (ADR-001-02)."""
    case_id, household_id = new_ids()
    owner = make_client(owner_url)
    try:
        await owner[integration_settings.MONGO_DB]["cases"].insert_one(
            case_document(case_id, household_id)
        )
    finally:
        await owner.close()
    return case_id, household_id


async def count(client: Client, settings: Settings, collection: str, query: dict[str, Any]) -> int:
    return await client[settings.MONGO_DB][collection].count_documents(query)


async def audit_count(client: Client, settings: Settings, reference_id: uuid.UUID) -> int:
    return await client[settings.MONGO_AUDIT_DB]["audit_log"].count_documents(
        {"reference_id": str(reference_id)}
    )


async def read_case(client: Client, settings: Settings, case_id: uuid.UUID) -> dict[str, Any]:
    document = await client[settings.MONGO_DB]["cases"].find_one({"_id": case_id})
    assert document is not None
    return document


def scope_of(change: CaseChange) -> Scope:
    return Scope.for_worker(change.case_id, change.household_id)


def override_change(case_id: uuid.UUID, household_id: uuid.UUID, seq: int) -> CaseChange:
    task_id = uuid.uuid5(case_id, "task")
    return approved(case_id, household_id, seq, override_reason=SYNTH_REASON, task_id=task_id)


async def test_FR_001_04_record_case_change_retry_is_noop(
    repository: CaseRepository,
    client: Client,
    integration_settings: Settings,
    case_ids: tuple[uuid.UUID, uuid.UUID],
) -> None:
    case_id, household_id = case_ids
    change = override_change(case_id, household_id, 1)

    first = await repository.apply(change, scope_of(change))
    second = await repository.apply(change, scope_of(change))

    assert (first, second) == (True, False)
    settings = integration_settings
    assert await count(client, settings, "case_events", {"case_id": case_id}) == 1
    assert await count(client, settings, "review_tasks", {"case_id": case_id}) == 1
    assert await count(client, settings, "coverage_periods", {"case_id": case_id}) == 1
    assert await count(client, settings, "outbox", {"case_id": case_id}) == 1
    assert await count(client, settings, "caseworker_actions", {"case_id": case_id}) == 1
    assert change.action is not None
    assert await audit_count(client, settings, change.action.action_id) == 1


async def test_FR_001_10_case_change_writes_event_with_times_actor_reason(
    repository: CaseRepository,
    client: Client,
    integration_settings: Settings,
    case_ids: tuple[uuid.UUID, uuid.UUID],
) -> None:
    case_id, household_id = case_ids
    change = move(case_id, household_id, 1, S.submitted, S.verifying)

    await repository.apply(change, scope_of(change))

    stored = await client[integration_settings.MONGO_DB]["case_events"].find_one(
        {"case_id": case_id, "seq": 1}
    )
    assert stored is not None
    assert stored["actor_type"] == "system" and stored["actor_id"] is None
    assert stored["reason_code"] == "workflow_started"
    assert (stored["from_state"], stored["to_state"]) == ("submitted", "verifying")
    assert stored["case_time"] == change.event.case_time
    assert stored["real_time"] == change.event.real_time
    case = await read_case(client, integration_settings, case_id)
    assert (case["state"], case["last_seq"]) == ("verifying", 1)


async def test_FR_001_10_stale_seq_does_not_overwrite_case(
    repository: CaseRepository,
    client: Client,
    integration_settings: Settings,
    case_ids: tuple[uuid.UUID, uuid.UUID],
) -> None:
    case_id, household_id = case_ids
    newer = move(case_id, household_id, 3, S.determining, S.under_review)
    older = move(case_id, household_id, 2, S.verifying, S.determining)

    await repository.apply(newer, scope_of(newer))
    await repository.apply(older, scope_of(older))

    case = await read_case(client, integration_settings, case_id)
    assert (case["state"], case["last_seq"]) == ("under_review", 3)
    # The late event is still kept in the history.
    assert await count(client, integration_settings, "case_events", {"case_id": case_id}) == 2


async def test_FR_001_20_override_outcome_stored_and_determination_unchanged(
    repository: CaseRepository,
    client: Client,
    integration_settings: Settings,
    case_ids: tuple[uuid.UUID, uuid.UUID],
) -> None:
    case_id, household_id = case_ids
    change = override_change(case_id, household_id, 1)

    await repository.apply(change, scope_of(change))

    case = await read_case(client, integration_settings, case_id)
    assert case["outcome"] == "approved" and case["open"] is False
    assert case["override"] == {
        "reviewer_id": REVIEWER,
        "reason": SYNTH_REASON,
        "determination_id": DETERMINATION_ID,
    }
    assert await count(client, integration_settings, "determinations", {}) == 0  # never written
    task = await client[integration_settings.MONGO_DB]["review_tasks"].find_one(
        {"case_id": case_id}
    )
    assert task is not None and task["status"] == ReviewTaskStatus.closed.value


async def test_FR_001_22_coverage_period_written_once_with_deterministic_id(
    repository: CaseRepository,
    client: Client,
    integration_settings: Settings,
    case_ids: tuple[uuid.UUID, uuid.UUID],
) -> None:
    case_id, household_id = case_ids
    change = approved(case_id, household_id, 1)

    await repository.apply(change, scope_of(change))
    await repository.apply(change, scope_of(change))

    periods = (
        await client[integration_settings.MONGO_DB]["coverage_periods"]
        .find({"household_id": household_id})
        .to_list()
    )
    assert len(periods) == 1
    assert periods[0]["_id"] == uuid.uuid5(case_id, "coverage")
    assert periods[0]["status"] == "active"


class FailingOutboxRepository(CaseRepository):
    async def _write_notice(
        self, change: CaseChange, scope: Scope, session: AsyncClientSession
    ) -> None:
        raise RuntimeError("outbox insert failed")


async def test_FR_001_04_failed_transaction_writes_nothing(
    client: Client,
    integration_settings: Settings,
    case_ids: tuple[uuid.UUID, uuid.UUID],
) -> None:
    case_id, household_id = case_ids
    audit = MongoAuditWriter(client, integration_settings.MONGO_AUDIT_DB)
    failing = FailingOutboxRepository(client, integration_settings.MONGO_DB, audit)
    change = override_change(case_id, household_id, 1)

    with pytest.raises(RuntimeError):
        await failing.apply(change, scope_of(change))

    settings = integration_settings
    for collection in ("case_events", "review_tasks", "caseworker_actions", "coverage_periods"):
        assert await count(client, settings, collection, {"case_id": case_id}) == 0
    case = await read_case(client, settings, case_id)
    assert (case["state"], case["last_seq"]) == ("submitted", 0)
    assert change.action is not None
    assert await audit_count(client, settings, change.action.action_id) == 0


async def test_FR_001_10_worker_scope_must_match_the_change(
    repository: CaseRepository, case_ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    from packages.platform.errors import Forbidden

    case_id, household_id = case_ids
    change = move(case_id, household_id, 1, S.submitted, S.verifying)

    with pytest.raises(Forbidden):
        await repository.apply(change, Scope.for_worker(uuid.uuid4(), household_id))
