"""Capture the household snapshot a determination is made from (FR-003-16, -17, -19; ADR-003-03).

One snapshot per `(case_id, as_of_date)`: an existing one is reused and never recaptured, so a
later household change can't alter a determination. Otherwise the members come from the stored
`household_members` of the as-of year, or, for a household with none, from the case's application
form (research R8); `VerifiedFacts` then adds identity and pay stub facts. Names are never copied.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID, uuid5

from apps.worker.repositories.snapshots import SnapshotSource
from apps.worker.repositories.verified_facts import VerifiedFacts
from packages.domain.case import ApplicationForm, CitizenshipStatus
from packages.domain.household import (
    HouseholdMember,
    HouseholdSnapshot,
    JobIncome,
    MemberFacts,
    VerifiedMemberFacts,
)
from packages.platform.clock import Clock
from packages.platform.errors import Forbidden
from packages.platform.scope import Scope


def snapshot_id_for(case_id: UUID, as_of_date: date) -> UUID:
    return uuid5(case_id, f"snapshot:{as_of_date.isoformat()}")


def _from_members(members: list[HouseholdMember]) -> list[MemberFacts]:
    return [
        MemberFacts(
            member_id=m.member_id,
            relationship=m.relationship,
            date_of_birth=m.date_of_birth,
            address_state=m.address_state,
            citizenship_attested=m.citizenship_attested,
            medicare_entitled=m.medicare_entitled,
            identity_verified=None,
            declared_zero_income=m.declared_zero_income,
            jobs=[JobIncome(job_id=job.job_id, stubs=[]) for job in m.jobs],
        )
        for m in members
    ]


def _from_form(case_id: UUID, form: ApplicationForm) -> list[MemberFacts]:
    members: list[MemberFacts] = []
    for index, applicant in enumerate(form.members):
        members.append(
            MemberFacts(
                member_id=uuid5(case_id, f"member:{index}"),
                relationship=applicant.relationship,
                date_of_birth=applicant.date_of_birth,
                address_state=form.address.state,
                citizenship_attested=applicant.citizenship_status is not CitizenshipStatus.neither,
                medicare_entitled=applicant.medicare_entitled,
                identity_verified=None,
                declared_zero_income=applicant.zero_income,
                jobs=[
                    JobIncome(job_id=uuid5(case_id, f"job:{index}:{n}"), stubs=[])
                    for n, _ in enumerate(applicant.jobs)
                ],
            )
        )
    return members


def _merge(member: MemberFacts, verified: VerifiedMemberFacts | None) -> MemberFacts:
    if verified is None:
        return member
    stubs = {job.job_id: job.stubs for job in verified.jobs}
    declared = {job.job_id for job in member.jobs}
    jobs = [JobIncome(job_id=j.job_id, stubs=stubs.get(j.job_id, j.stubs)) for j in member.jobs]
    jobs += [job for job in verified.jobs if job.job_id not in declared]
    return member.model_copy(update={"identity_verified": verified.identity_verified, "jobs": jobs})


class SnapshotCapture:
    def __init__(self, source: SnapshotSource, verified: VerifiedFacts, clock: Clock) -> None:
        self._source = source
        self._verified = verified
        self._clock = clock

    async def capture(
        self, scope: Scope, as_of_date: date, held_year: int | None = None
    ) -> HouseholdSnapshot:
        if scope.case_id is None or scope.household_id is None:
            raise Forbidden()
        existing = await self._source.snapshot_for(scope, as_of_date)
        if existing is not None:
            return existing
        stored = await self._source.members_for_year(scope, held_year or as_of_date.year)
        if stored:
            members = _from_members(stored)
        else:
            form = await self._source.application_form(scope)
            members = _from_form(scope.case_id, form) if form is not None else []
        verified = {v.member_id: v for v in await self._verified.for_household(scope)}
        snapshot = HouseholdSnapshot(
            snapshot_id=snapshot_id_for(scope.case_id, as_of_date),
            household_id=scope.household_id,
            case_id=scope.case_id,
            as_of_date=as_of_date,
            captured_at=self._clock.now(),
            members=[_merge(m, verified.get(m.member_id)) for m in members],
        )
        if await self._source.insert_snapshot(scope, snapshot):
            return snapshot
        # An earlier attempt stored it first (TW-11): that one is the snapshot of record.
        stored_snapshot = await self._source.snapshot_for(scope, as_of_date)
        return stored_snapshot if stored_snapshot is not None else snapshot
