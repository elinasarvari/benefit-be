"""In-memory ports and synthetic households for the determination activity tests (003, PR-3).

No real names: every name is a `SYNTH-` marker. IDs are fixed so failures read the same each run.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from apps.worker.activities.determination import DeterminationActivities
from apps.worker.activities.snapshot import SnapshotCapture
from packages.domain.case import ApplicationForm, PayFrequency, Relationship
from packages.domain.eligibility import DeterminationRecord
from packages.domain.household import (
    HouseholdMember,
    HouseholdSnapshot,
    Job,
    JobIncome,
    PayStubFact,
    VerifiedMemberFacts,
)
from packages.platform.clock import FixedClock
from packages.platform.scope import Scope

HOUSEHOLD = uuid.UUID("00000000-0000-4000-8000-0000000003a1")
OTHER_HOUSEHOLD = uuid.UUID("00000000-0000-4000-8000-0000000003b2")
CASE = uuid.UUID("00000000-0000-4000-8000-0000000003c1")
AS_OF = date(2026, 3, 17)
NOW = datetime(2026, 3, 17, 17, 0, tzinfo=UTC)
SELF_ID = uuid.UUID("00000000-0000-4000-8000-000000000a01")
SPOUSE_ID = uuid.UUID("00000000-0000-4000-8000-000000000a02")
SELF_JOB = uuid.UUID("00000000-0000-4000-8000-000000000e01")
SPOUSE_JOB = uuid.UUID("00000000-0000-4000-8000-000000000e02")


def scope() -> Scope:
    return Scope.for_worker(CASE, HOUSEHOLD)


def stored_member(
    member_id: uuid.UUID = SELF_ID,
    *,
    relationship: Relationship = Relationship.self,
    year: int = 2026,
    state: str = "IL",
    zero_income: bool = True,
    job_id: uuid.UUID | None = None,
    household_id: uuid.UUID = HOUSEHOLD,
) -> HouseholdMember:
    return HouseholdMember(
        member_id=member_id,
        household_id=household_id,
        given_name="SYNTH-GIVEN",
        family_name="SYNTH-FAMILY",
        date_of_birth=date(1990, 5, 1),
        relationship=relationship,
        address_state=state,
        citizenship_attested=True,
        medicare_entitled=False,
        declared_zero_income=zero_income,
        jobs=(
            []
            if job_id is None
            else [
                Job(
                    job_id=job_id,
                    employer="SYNTH-EMPLOYER",
                    declared_frequency=PayFrequency.weekly,
                    declared_gross=Decimal("500.00"),
                )
            ]
        ),
        coverage_year=year,
    )


def verified(
    member_id: uuid.UUID = SELF_ID,
    *,
    identity: bool | None = True,
    job_id: uuid.UUID | None = None,
    weekly_gross: str | None = None,
) -> VerifiedMemberFacts:
    jobs: list[JobIncome] = []
    if job_id is not None:
        stubs = (
            []
            if weekly_gross is None
            else [
                PayStubFact(
                    document_id=uuid.uuid5(job_id, "stub"),
                    pay_date=date(2026, 3, 13),
                    frequency=PayFrequency.weekly,
                    gross_pay=Decimal(weekly_gross),
                )
            ]
        )
        jobs.append(JobIncome(job_id=job_id, stubs=stubs))
    return VerifiedMemberFacts(
        household_id=HOUSEHOLD, member_id=member_id, identity_verified=identity, jobs=jobs
    )


@dataclass
class FakeSource:
    """`SnapshotSource` over dicts; counts how often the snapshot was inserted."""

    snapshots: dict[tuple[uuid.UUID, date], HouseholdSnapshot] = field(default_factory=dict)
    members: list[HouseholdMember] = field(default_factory=list)
    form: ApplicationForm | None = None
    insert_attempts: int = 0

    async def snapshot_for(self, scope: Scope, as_of_date: date) -> HouseholdSnapshot | None:
        assert scope.case_id is not None
        return self.snapshots.get((scope.case_id, as_of_date))

    async def members_for_year(self, scope: Scope, year: int) -> list[HouseholdMember]:
        return [
            m
            for m in self.members
            if m.household_id == scope.household_id and m.coverage_year == year
        ]

    async def application_form(self, scope: Scope) -> ApplicationForm | None:
        return self.form

    async def insert_snapshot(self, scope: Scope, snapshot: HouseholdSnapshot) -> bool:
        self.insert_attempts += 1
        key = (snapshot.case_id, snapshot.as_of_date)
        if key in self.snapshots:
            return False
        self.snapshots[key] = snapshot
        return True


@dataclass
class FakeVerified:
    facts: list[VerifiedMemberFacts] = field(default_factory=list)
    scopes: list[Scope] = field(default_factory=list)

    async def for_household(self, scope: Scope) -> list[VerifiedMemberFacts]:
        self.scopes.append(scope)
        return [f for f in self.facts if f.household_id == scope.household_id]


@dataclass
class FakeStore:
    """`DeterminationStore` keyed by `_id`; the first `failures` inserts raise (an outage)."""

    records: dict[uuid.UUID, DeterminationRecord] = field(default_factory=dict)
    failures: int = 0
    attempts: int = 0

    async def insert(self, scope: Scope, record: DeterminationRecord) -> bool:
        self.attempts += 1
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("synthetic database outage")
        if record.determination_id in self.records:
            return False
        self.records[record.determination_id] = record
        return True


def capture(source: FakeSource, facts: FakeVerified) -> SnapshotCapture:
    return SnapshotCapture(source, facts, FixedClock(NOW))


def activities(
    source: FakeSource, facts: FakeVerified, store: FakeStore
) -> DeterminationActivities:
    return DeterminationActivities(capture(source, facts), store, FixedClock(NOW))
