"""`apply_verification_action`: corrections and dismissals, once per action.

FR-002-19; ADR-002-06.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from apps.worker.activities.verification import VerificationActivities
from apps.worker.repositories.case_members import CaseMembers
from apps.worker.repositories.pay_stubs import EnteredPayStubs, MongoPayStubs
from apps.worker.repositories.reference_records import MongoReferenceRecords
from apps.worker.repositories.snapshots import MongoSnapshotSource
from packages.domain.household import VerifiedMemberFacts
from packages.domain.review import ActionType, CorrectedValue
from packages.domain.verification import FindingCause, VerificationRequest
from packages.platform.clock import FixedClock
from packages.platform.mongo import Client
from packages.platform.scope import Scope
from tests.apps.worker.verification_support import (
    AS_OF,
    CASE,
    DOCUMENT,
    HOUSEHOLD,
    REVIEWER,
    SCOPE,
    SUBMITTED,
    SUBMITTED_MICROS,
    MemoryMembers,
    MemoryReferences,
    MemoryStore,
    action,
    correct_gross,
    seed_case,
)

REQUEST = VerificationRequest(case_id=CASE, household_id=HOUSEHOLD, as_of_date=AS_OF)
THRESHOLD = Decimal("10")


class NoSeeds:
    async def for_household(self, scope: Scope) -> list[VerifiedMemberFacts]:
        return []


def _memory_activities() -> tuple[VerificationActivities, MemoryStore]:
    store = MemoryStore()
    activities = VerificationActivities(
        MemoryMembers(), store, MemoryReferences(), FixedClock(SUBMITTED), THRESHOLD
    )
    return activities, store


def _mongo_activities(mongo: Client, db_name: str) -> tuple[VerificationActivities, MongoPayStubs]:
    store = MongoPayStubs(mongo, db_name)
    activities = VerificationActivities(
        CaseMembers(MongoSnapshotSource(mongo, db_name)),
        store,
        MongoReferenceRecords(mongo, db_name),
        FixedClock(SUBMITTED),
        THRESHOLD,
    )
    return activities, store


@pytest.mark.integration
async def test_FR_002_19_correction_recorded_and_check_reruns(
    mongo: Client, owner_url: str, db_name: str
) -> None:
    await seed_case(owner_url, db_name)
    activities, store = _mongo_activities(mongo, db_name)
    first = await ActivityEnvironment().run(activities.verify_documents, REQUEST)
    assert [f.cause for f in first.findings] == [FindingCause.income_mismatch]

    result = await ActivityEnvironment().run(
        activities.apply_verification_action, correct_gross("2000.00")
    )

    assert result.findings == [] and result.needs_review is False  # the mismatch cleared
    (stub,) = await store.details(SCOPE)
    (correction,) = stub.corrections
    assert (correction.field, correction.old_value, correction.new_value) == (
        "gross_pay",
        "3000.00",
        "2000.00",
    )
    assert correction.reviewer_id == REVIEWER and correction.corrected_at == SUBMITTED
    assert correction.reason  # the reason is stored with the correction
    # The snapshot's source now reads the corrected value (ADR-002-04).
    entered = EnteredPayStubs(
        store, CaseMembers(MongoSnapshotSource(mongo, db_name)), NoSeeds(), FixedClock(SUBMITTED)
    )
    (member,) = await entered.for_household(SCOPE)
    assert member.jobs[0].stubs[0].gross_pay == Decimal("2000.00")
    assert member.identity_verified is True


@pytest.mark.integration
async def test_FR_002_19_action_replay_same_action_id_applied_once(
    mongo: Client, owner_url: str, db_name: str
) -> None:
    await seed_case(owner_url, db_name)
    activities, store = _mongo_activities(mongo, db_name)
    await ActivityEnvironment().run(activities.verify_documents, REQUEST)
    # A real API timestamp has microseconds; BSON keeps milliseconds, so only `action_id` can
    # tell the redelivery from a new action (TC-28).
    delivered_twice = correct_gross("2000.00").model_copy(update={"submitted_at": SUBMITTED_MICROS})

    first = await ActivityEnvironment().run(activities.apply_verification_action, delivered_twice)
    second = await ActivityEnvironment().run(activities.apply_verification_action, delivered_twice)

    assert first == second
    (stub,) = await store.details(SCOPE)
    assert len(stub.corrections) == 1  # TW-11: one correction for one action_id


@pytest.mark.integration
async def test_FR_002_19_dismissal_recorded_with_reviewer_and_time(
    mongo: Client, owner_url: str, db_name: str
) -> None:
    await seed_case(owner_url, db_name)
    activities, store = _mongo_activities(mongo, db_name)
    first = await ActivityEnvironment().run(activities.verify_documents, REQUEST)
    (finding,) = first.findings

    result = await ActivityEnvironment().run(
        activities.apply_verification_action,
        action(ActionType.dismiss_finding, finding_id=finding.finding_id),
    )

    (dismissed,) = result.findings
    assert dismissed.finding_id == finding.finding_id  # same finding, no longer open
    assert dismissed.dismissed is not None
    assert (dismissed.dismissed.reviewer_id, dismissed.dismissed.dismissed_at) == (
        REVIEWER,
        SUBMITTED,
    )
    assert result.needs_review is False
    stored = await store.stored_result(SCOPE)
    assert stored is not None and stored.findings[0].dismissed is not None
    # The next plain run keeps the dismissal (FR-002-24: it counts as handled).
    again = await ActivityEnvironment().run(activities.verify_documents, REQUEST)
    assert again.findings[0].dismissed is not None


@pytest.mark.parametrize("reason", [None, "", "   \n"])
async def test_FR_002_19_blank_reason_rejected_by_activity(reason: str | None) -> None:
    activities, store = _memory_activities()
    await ActivityEnvironment().run(activities.verify_documents, REQUEST)
    saves, rows = store.saves, list(store.rows)
    blank = correct_gross("2000.00").model_copy(update={"reason": reason})

    with pytest.raises(ApplicationError) as raised:
        await ActivityEnvironment().run(activities.apply_verification_action, blank)

    assert raised.value.message == "reason_required" and raised.value.non_retryable
    assert store.saves == saves and store.rows == rows  # nothing recorded


async def test_FR_002_19_unknown_finding_or_field_rejected() -> None:
    activities, store = _memory_activities()
    await ActivityEnvironment().run(activities.verify_documents, REQUEST)
    saves, rows = store.saves, list(store.rows)
    other_case_finding = action(ActionType.dismiss_finding, finding_id=uuid4())
    not_a_detail = action(
        ActionType.correct_value,
        corrected_value=CorrectedValue(field_ref=f"pay_stub:{DOCUMENT}:ssn", value="x"),
    )
    other_document = correct_gross("2000.00", document_id=uuid4())
    bad_value = correct_gross("not a number")

    for rejected, code in [
        (other_case_finding, "unknown_finding"),
        (not_a_detail, "unknown_field"),
        (other_document, "unknown_field"),
        (bad_value, "invalid_value"),
    ]:
        with pytest.raises(ApplicationError) as raised:
            await ActivityEnvironment().run(activities.apply_verification_action, rejected)
        assert raised.value.message == code and raised.value.non_retryable

    assert store.saves == saves and store.rows == rows


async def test_FR_002_19_replay_with_microseconds_applied_once_in_memory() -> None:
    activities, store = _memory_activities()
    await ActivityEnvironment().run(activities.verify_documents, REQUEST)
    delivered = correct_gross("2000.00").model_copy(update={"submitted_at": SUBMITTED_MICROS})

    await ActivityEnvironment().run(activities.apply_verification_action, delivered)
    # The retry carries the same action_id with a different field-for-field timestamp.
    retried = delivered.model_copy(update={"submitted_at": SUBMITTED_MICROS.replace(microsecond=0)})
    await ActivityEnvironment().run(activities.apply_verification_action, retried)

    (stub,) = store.rows
    assert [c.action_id for c in stub.corrections] == [delivered.action_id]
