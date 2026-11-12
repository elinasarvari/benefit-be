"""The members and declared jobs a case's pay stubs are checked against (ADR-002-04).

Same source and the same IDs as the determination snapshot (`activities/snapshot.py`): stored
`household_members` of the as-of year, else the application form with `uuid5(case_id, "member:i")`
and `uuid5(case_id, "job:i:n")`. The rules engine and the check must agree on who and which job.
Names stay in the worker: they never reach `MemberFacts`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid5

from apps.worker.repositories.snapshots import SnapshotSource
from packages.domain.case import PayFrequency
from packages.platform.errors import Forbidden
from packages.platform.scope import Scope


@dataclass(frozen=True)
class CaseJob:
    job_id: UUID
    employer: str
    gross: Decimal
    frequency: PayFrequency


@dataclass(frozen=True)
class CaseMember:
    member_id: UUID
    given_name: str
    family_name: str
    date_of_birth: date
    zero_income: bool
    jobs: list[CaseJob] = field(default_factory=list)

    @property
    def full_name(self) -> str:
        return " ".join(part for part in (self.given_name, self.family_name) if part)


class CaseMembers:
    def __init__(self, source: SnapshotSource) -> None:
        self._source = source

    async def for_case(self, scope: Scope, as_of_date: date) -> list[CaseMember]:
        if scope.case_id is None or scope.household_id is None:
            raise Forbidden()
        stored = await self._source.members_for_year(scope, as_of_date.year)
        if stored:
            return [
                CaseMember(
                    member_id=m.member_id,
                    given_name=m.given_name,
                    family_name=m.family_name,
                    date_of_birth=m.date_of_birth,
                    zero_income=m.declared_zero_income,
                    jobs=[
                        CaseJob(j.job_id, j.employer, j.declared_gross, j.declared_frequency)
                        for j in m.jobs
                    ],
                )
                for m in stored
            ]
        form = await self._source.application_form(scope)
        if form is None:
            return []
        members: list[CaseMember] = []
        for index, applicant in enumerate(form.members):
            given, _, family = applicant.full_name.strip().partition(" ")
            members.append(
                CaseMember(
                    member_id=uuid5(scope.case_id, f"member:{index}"),
                    given_name=given,
                    family_name=family.strip(),
                    date_of_birth=applicant.date_of_birth,
                    zero_income=applicant.zero_income,
                    jobs=[
                        CaseJob(
                            uuid5(scope.case_id, f"job:{index}:{n}"),
                            job.employer,
                            job.declared_monthly_gross,
                            PayFrequency.monthly,
                        )
                        for n, job in enumerate(applicant.jobs)
                    ],
                )
            )
        return members
