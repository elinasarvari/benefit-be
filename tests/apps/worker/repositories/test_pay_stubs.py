"""Reference records and entered pay stubs on MongoDB (FR-002-22, FR-002-24; ADR-002-04)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from temporalio.testing import ActivityEnvironment

from apps.worker.activities.verification import VerificationActivities
from apps.worker.repositories.case_members import CaseMembers
from apps.worker.repositories.pay_stubs import EnteredPayStubs, MongoPayStubs
from apps.worker.repositories.reference_records import MongoReferenceRecords
from apps.worker.repositories.snapshots import MongoSnapshotSource
from packages.domain.household import VerifiedMemberFacts
from packages.domain.review import ActionType
from packages.domain.verification import (
    FindingCause,
    IdentityMatch,
    ReferenceLookup,
    VerificationRequest,
)
from packages.platform.clock import FixedClock
from packages.platform.mongo import Client
from packages.platform.scope import Scope
from tests.apps.worker.verification_support import (
    AS_OF,
    CASE,
    DOB,
    FAMILY,
    GIVEN,
    HOUSEHOLD,
    MEMBER,
    SCOPE,
    SUBMITTED,
    action,
    seed_case,
)

pytestmark = pytest.mark.integration

REQUEST = VerificationRequest(case_id=CASE, household_id=HOUSEHOLD, as_of_date=AS_OF)


class NoSeeds:
    async def for_household(self, scope: Scope) -> list[VerifiedMemberFacts]:
        return []


def _lookup(*, given: str = GIVEN, dob: date = DOB) -> ReferenceLookup:
    return ReferenceLookup(
        household_id=HOUSEHOLD,
        member_id=MEMBER,
        given_name=given,
        family_name=FAMILY,
        date_of_birth=dob,
    )


def _wiring(app_client: Client, db_name: str) -> tuple[VerificationActivities, EnteredPayStubs]:
    store = MongoPayStubs(app_client, db_name)
    members = CaseMembers(MongoSnapshotSource(app_client, db_name))
    activities = VerificationActivities(
        members,
        store,
        MongoReferenceRecords(app_client, db_name),
        FixedClock(SUBMITTED),
        Decimal("10"),
    )
    return activities, EnteredPayStubs(store, members, NoSeeds(), FixedClock(SUBMITTED))


async def test_FR_002_22_identity_match_sets_identity_verified(
    app_client: Client, owner_url: str, db_name: str
) -> None:
    await seed_case(owner_url, db_name)
    references = MongoReferenceRecords(app_client, db_name)
    # Exact on case-folded names and the date of birth.
    assert (await references.lookup(SCOPE, _lookup(given=GIVEN.upper()))).identity is (
        IdentityMatch.match
    )
    answer = await references.lookup(SCOPE, _lookup(dob=date(1991, 5, 4)))
    assert answer.identity is IdentityMatch.no_match and answer.jobs == []
    unknown = _lookup().model_copy(update={"member_id": uuid4()})
    assert (await references.lookup(SCOPE, unknown)).identity is IdentityMatch.no_record

    activities, entered = _wiring(app_client, db_name)
    await ActivityEnvironment().run(activities.verify_documents, REQUEST)

    (facts,) = await entered.for_household(SCOPE)
    assert facts.identity_verified is True and facts.member_id == MEMBER
    assert [s.gross_pay for s in facts.jobs[0].stubs] == [Decimal("3000.00")]


async def test_FR_002_24_dismissed_identity_mismatch_counts_as_verified(
    app_client: Client, owner_url: str, db_name: str
) -> None:
    await seed_case(owner_url, db_name, reference_dob=date(DOB.year - 1, DOB.month, DOB.day))
    activities, entered = _wiring(app_client, db_name)
    result = await ActivityEnvironment().run(activities.verify_documents, REQUEST)
    (mismatch,) = [f for f in result.findings if f.cause is FindingCause.identity_mismatch]
    (facts,) = await entered.for_household(SCOPE)
    assert facts.identity_verified is False  # open mismatch: not verified

    await ActivityEnvironment().run(
        activities.apply_verification_action,
        action(ActionType.dismiss_finding, finding_id=mismatch.finding_id),
    )

    (facts,) = await entered.for_household(SCOPE)
    assert facts.identity_verified is True  # dismissed with a reason: counts as verified
