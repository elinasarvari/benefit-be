"""`verify_documents`: thin, idempotent, no partial write (FR-002-23, FR-002-03; TW-9, TW-11)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from temporalio.testing import ActivityEnvironment

from apps.worker.activities.verification import VerificationActivities
from apps.worker.repositories.case_members import CaseMembers
from apps.worker.repositories.pay_stubs import MongoPayStubs
from apps.worker.repositories.reference_records import MongoReferenceRecords
from apps.worker.repositories.snapshots import MongoSnapshotSource
from packages.domain.verification import VerificationRequest, VerificationResult
from packages.platform.clock import FixedClock
from packages.platform.mongo import Client
from packages.rules.verification import CheckInput
from tests.apps.worker.verification_support import (
    AS_OF,
    CASE,
    HOUSEHOLD,
    SUBMITTED,
    MemoryMembers,
    MemoryReferences,
    MemoryStore,
    seed_case,
)

REQUEST = VerificationRequest(case_id=CASE, household_id=HOUSEHOLD, as_of_date=AS_OF)
THRESHOLD = Decimal("10")


def _activities(
    members: Any, store: Any, references: Any, check: Any = None
) -> VerificationActivities:
    extra = {"check": check} if check is not None else {}
    return VerificationActivities(
        members, store, references, FixedClock(SUBMITTED), THRESHOLD, **extra
    )


async def test_FR_002_23_verify_documents_thin_activity_no_branching() -> None:
    expected = VerificationResult(complete=True, needs_review=False, case_id=CASE)
    seen: list[tuple[CheckInput, Decimal]] = []

    def check(data: CheckInput, *, threshold_percent: Decimal) -> VerificationResult:
        seen.append((data, threshold_percent))
        return expected

    members, store, references = MemoryMembers(), MemoryStore(), MemoryReferences()
    activities = _activities(members, store, references, check)

    result = await ActivityEnvironment().run(activities.verify_documents, REQUEST)

    assert result == expected
    assert store.result == expected and store.saves == 1  # stored once, as the check returned it
    assert len(seen) == 1 and seen[0][1] == THRESHOLD  # the configured threshold, not a literal
    data = seen[0][0]
    assert data.case_id == CASE and data.as_of_date == AS_OF
    assert [m.member_id for m in data.members] == [m.member_id for m in members.members]
    assert data.stubs == store.rows
    assert (
        len(references.lookups) == 1
    )  # one lookup per member, with the stored name and birth date


async def test_FR_002_23_verify_documents_repo_failure_propagates_retryable() -> None:
    store = MemoryStore()
    references = MemoryReferences(error=ConnectionError("reference store unavailable"))
    activities = _activities(MemoryMembers(), store, references)

    with pytest.raises(ConnectionError):  # not an ApplicationError: Temporal retries it
        await ActivityEnvironment().run(activities.verify_documents, REQUEST)

    assert store.saves == 0 and store.result is None  # no partial write


@pytest.mark.integration
async def test_FR_002_23_verify_documents_idempotent_and_stored(
    mongo: Client, owner_url: str, db_name: str
) -> None:
    await seed_case(owner_url, db_name)
    activities = VerificationActivities(
        CaseMembers(MongoSnapshotSource(mongo, db_name)),
        MongoPayStubs(mongo, db_name),
        MongoReferenceRecords(mongo, db_name),
        FixedClock(SUBMITTED),
        THRESHOLD,
    )

    first = await ActivityEnvironment().run(activities.verify_documents, REQUEST)
    second = await ActivityEnvironment().run(activities.verify_documents, REQUEST)

    assert first == second and first.needs_review  # 3000 documented against 2000 declared
    stored = await mongo[db_name]["verification_results"].find({"case_id": CASE}).to_list(None)
    assert len(stored) == 1  # one stored result for the case, not two (TW-11)
    assert stored[0]["synthetic"] is True
