"""The audit entry is part of the case-change transaction across databases (001; TC-70; R7).

`record_case_change` writes `benefit` collections and the `benefit_audit` entry in one
transaction. If the audit write fails, nothing of the change may stay; if it succeeds, the entry
commits with the change. If this fails, raise an ADR amendment rather than weaken the test.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from pymongo.asynchronous.client_session import AsyncClientSession

from apps.api.repositories.cases import MongoCaseRepository
from apps.worker.repositories.cases import CaseRepository
from packages.domain.audit import ActorType, AuditAction, AuditLogEntry, AuditOutcome
from packages.domain.auth import Role
from packages.domain.case import ActionOutcome, ActionType, CaseChange, CaseworkerAction
from packages.domain.review import RejectionCode
from packages.platform.audit import MongoAuditWriter
from packages.platform.mongo import Client, make_client
from packages.platform.scope import Scope, ScopeKind
from packages.platform.settings import Settings
from tests.apps.worker.case_factories import SYNTH_REASON, approved, case_document, new_ids

CASE_COLLECTIONS = (
    "case_events",
    "review_tasks",
    "caseworker_actions",
    "coverage_periods",
    "outbox",
)


class AuditFailsAfterWrite(MongoAuditWriter):
    """Writes the audit entry inside the transaction, then fails, so the whole change must abort."""

    async def write(self, entry: AuditLogEntry, session: AsyncClientSession | None = None) -> None:
        await super().write(entry, session=session)
        raise RuntimeError("synthetic benefit_audit failure")


async def insert_case(settings: Settings) -> tuple[uuid.UUID, uuid.UUID]:
    case_id, household_id = new_ids()
    owner = make_client(settings.MONGO_URL_OWNER.get_secret_value())
    try:
        await owner[settings.MONGO_DB]["cases"].insert_one(case_document(case_id, household_id))
    finally:
        await owner.close()
    return case_id, household_id


def override(case_id: uuid.UUID, household_id: uuid.UUID) -> CaseChange:
    return approved(
        case_id, household_id, 1, override_reason=SYNTH_REASON, task_id=uuid.uuid5(case_id, "task")
    )


async def state(client: Client, settings: Settings, change: CaseChange) -> dict[str, Any]:
    db = client[settings.MONGO_DB]
    assert change.action is not None
    counts = {c: await db[c].count_documents({"case_id": change.case_id}) for c in CASE_COLLECTIONS}
    case = await db["cases"].find_one({"_id": change.case_id})
    assert case is not None
    audit = await client[settings.MONGO_AUDIT_DB]["audit_log"].count_documents(
        {"reference_id": str(change.action.action_id)}
    )
    return {**counts, "case": (case["state"], case["last_seq"]), "audit": audit}


async def test_FR_001_10_audit_entry_commits_and_rolls_back_with_case_change(
    integration_settings: Settings,
) -> None:
    settings = integration_settings
    client = make_client(settings.MONGO_URL_APP.get_secret_value())
    try:
        failing = CaseRepository(
            client, settings.MONGO_DB, AuditFailsAfterWrite(client, settings.MONGO_AUDIT_DB)
        )
        working = CaseRepository(
            client, settings.MONGO_DB, MongoAuditWriter(client, settings.MONGO_AUDIT_DB)
        )
        rolled_back = override(*await insert_case(settings))
        committed = override(*await insert_case(settings))

        with pytest.raises(RuntimeError, match="synthetic benefit_audit failure"):
            await failing.apply(
                rolled_back, Scope.for_worker(rolled_back.case_id, rolled_back.household_id)
            )
        applied = await working.apply(
            committed, Scope.for_worker(committed.case_id, committed.household_id)
        )

        after_failure = await state(client, settings, rolled_back)
        after_success = await state(client, settings, committed)
    finally:
        await client.close()

    assert after_failure == {
        **{c: 0 for c in CASE_COLLECTIONS},
        "case": ("submitted", 0),
        "audit": 0,
    }
    assert applied is True
    assert after_success == {
        **{c: 1 for c in CASE_COLLECTIONS},
        "case": ("decided", 1),
        "audit": 1,
    }


async def test_FR_001_02_api_refusal_recorded_once_with_audit_entry(
    integration_settings: Settings,
) -> None:
    """ADR-001-04: a refusal on a decided case is stored by the API, keyed by request_id."""
    settings = integration_settings
    case_id, household_id = await insert_case(settings)
    reviewer, request_id, now = uuid.uuid4(), uuid.uuid4(), datetime.now(UTC)
    action = CaseworkerAction(
        action_id=request_id,
        task_id=uuid.uuid5(case_id, "task"),
        action=ActionType.override_to_approve,
        reviewer_id=reviewer,
        reviewer_role=Role.caseworker,
        reason=SYNTH_REASON,
        submitted_at=now,
        household_id=household_id,
        case_id=case_id,
        outcome=ActionOutcome.rejected,
        rejection_code=RejectionCode.task_closed,
        recorded_at=now,
    )
    entry = AuditLogEntry(
        occurred_at=now,
        actor_type=ActorType.user,
        actor_id=str(reviewer),
        action=AuditAction.caseworker_action,
        target_type="case",
        target_id=str(case_id),
        outcome=AuditOutcome.refused,
        reference_id=str(request_id),
    )
    scope = Scope(ScopeKind.user, role=Role.caseworker)
    client = make_client(settings.MONGO_URL_APP.get_secret_value())
    try:
        repo = MongoCaseRepository(client, settings.MONGO_DB)
        audit = MongoAuditWriter(client, settings.MONGO_AUDIT_DB)
        await repo.record_rejected_action(scope, action, entry, audit)
        await repo.record_rejected_action(scope, action, entry, audit)  # a retried request
        stored = client[settings.MONGO_DB]["caseworker_actions"].find({"_id": request_id})
        documents = await stored.to_list()
        audited = await client[settings.MONGO_AUDIT_DB]["audit_log"].count_documents(
            {"reference_id": str(request_id)}
        )
    finally:
        await client.close()

    [document] = documents
    assert (document["outcome"], document["action"]) == ("rejected", "override_to_approve")
    assert (document["case_id"], document["household_id"]) == (case_id, household_id)
    assert audited == 1
