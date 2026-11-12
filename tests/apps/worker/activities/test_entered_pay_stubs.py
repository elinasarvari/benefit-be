"""`EnteredPayStubs` reuses the check's stub assignment (ADR-002-04, FR-002-23). No database."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from temporalio.testing import ActivityEnvironment

from apps.worker.activities.verification import VerificationActivities
from apps.worker.repositories.case_members import CaseJob, CaseMember
from apps.worker.repositories.pay_stubs import EnteredPayStubs
from packages.domain.case import PayFrequency
from packages.domain.household import VerifiedMemberFacts
from packages.domain.verification import VerificationRequest
from packages.platform.clock import FixedClock
from packages.platform.scope import Scope
from tests.apps.worker.verification_support import (
    AS_OF,
    CASE,
    DOB,
    EMPLOYER,
    FAMILY,
    GIVEN,
    HOUSEHOLD,
    JOB,
    MEMBER,
    SCOPE,
    SUBMITTED,
    MemoryMembers,
    MemoryReferences,
    MemoryStore,
    case_member,
    stub_details,
)

REQUEST = VerificationRequest(case_id=CASE, household_id=HOUSEHOLD, as_of_date=AS_OF)


class NoSeeds:
    async def for_household(self, scope: Scope) -> list[VerifiedMemberFacts]:
        return []


async def _facts(members: list[CaseMember], store: MemoryStore) -> list[VerifiedMemberFacts]:
    memory_members = MemoryMembers(members)
    activities = VerificationActivities(
        memory_members, store, MemoryReferences(), FixedClock(SUBMITTED), Decimal("10")
    )
    await ActivityEnvironment().run(activities.verify_documents, REQUEST)
    entered = EnteredPayStubs(store, memory_members, NoSeeds(), FixedClock(SUBMITTED))
    return await entered.for_household(SCOPE)


async def test_FR_002_23_facts_follow_the_checks_match_for_a_spaced_name() -> None:
    spaced = stub_details().model_copy(update={"employee_name": f"{GIVEN.upper()}  {FAMILY}"})

    (facts,) = await _facts([case_member()], MemoryStore([spaced]))

    assert [s.document_id for s in facts.jobs[0].stubs] == [spaced.document_id]


async def test_FR_002_23_one_stub_counts_for_one_of_two_jobs_at_one_employer() -> None:
    member = CaseMember(
        member_id=MEMBER,
        given_name=GIVEN,
        family_name=FAMILY,
        date_of_birth=DOB,
        zero_income=False,
        jobs=[
            CaseJob(JOB, EMPLOYER, Decimal("3000.00"), PayFrequency.monthly),
            CaseJob(UUID(int=JOB.int + 1), EMPLOYER, Decimal("1000.00"), PayFrequency.monthly),
        ],
    )

    (facts,) = await _facts([member], MemoryStore([stub_details(gross="3000.00")]))

    assert sorted(len(job.stubs) for job in facts.jobs) == [0, 1]  # income not counted twice
    assert next(job for job in facts.jobs if job.stubs).job_id == JOB


def test_FR_002_14_one_word_member_name_is_not_doubled() -> None:
    one_word = CaseMember(
        member_id=MEMBER, given_name="Ana", family_name="", date_of_birth=DOB, zero_income=True
    )

    assert one_word.full_name == "Ana"
