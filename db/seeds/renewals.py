"""Reference records for 007's demo households E and F (ADR-007-05, FR-007-13). All synthetic.

E's records agree with held data, so it renews automatically. F's first job reports income 20%
above held, so its request lists only that job. Other households have no records yet (002's
seed task S-01 adds them), so the lookup finds no identity for them.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID

from db.seeds.households import SeededDataset, SeededHousehold
from db.seeds.ids import HOUSEHOLD_E, HOUSEHOLD_F, seeded_id
from packages.domain.household import HouseholdMember
from packages.domain.verification import IdentityMatch, ReferenceAnswer, ReferenceJob
from packages.rules.renewal import held_monthly

RENEWAL_HOUSEHOLDS = (HOUSEHOLD_E, HOUSEHOLD_F)
RENEWAL_YEAR = 2026
DIFFERENCE = Decimal("1.20")
SEEDED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def _renewal_households(dataset: SeededDataset) -> list[SeededHousehold]:
    return [h for h in dataset.households if h.household_id in RENEWAL_HOUSEHOLDS]


def _jobs(household: SeededHousehold, member: HouseholdMember) -> list[ReferenceJob]:
    first_job = next(j.job_id for m in household_members(household) for j in m.jobs)
    jobs = []
    for job in member.jobs:
        monthly = held_monthly(job)
        if household.household_id == HOUSEHOLD_F and job.job_id == first_job:
            monthly = (monthly * DIFFERENCE).quantize(Decimal("0.01"))
        jobs.append(ReferenceJob(employer=job.employer, monthly_gross=monthly))
    return jobs


def household_members(household: SeededHousehold) -> list[HouseholdMember]:
    return household.years[RENEWAL_YEAR].members


def reference_answers(dataset: SeededDataset) -> dict[UUID, list[ReferenceAnswer]]:
    """What the lookup answers per household (the same data `reference_record_documents` writes)."""
    return {
        h.household_id: [
            ReferenceAnswer(member_id=m.member_id, identity=IdentityMatch.match, jobs=_jobs(h, m))
            for m in household_members(h)
        ]
        for h in _renewal_households(dataset)
    }


def reference_record_documents(dataset: SeededDataset) -> list[dict[str, Any]]:
    documents = []
    for household in _renewal_households(dataset):
        for member in household_members(household):
            documents.append(
                {
                    "_id": seeded_id(f"reference-{member.member_id}"),
                    "household_id": household.household_id,
                    "member_id": member.member_id,
                    "given_name": member.given_name,
                    "family_name": member.family_name,
                    "date_of_birth": datetime.combine(member.date_of_birth, time.min, tzinfo=UTC),
                    "jobs": [
                        {"employer": job.employer, "monthly_gross": str(job.monthly_gross)}
                        for job in _jobs(household, member)
                    ],
                    "synthetic": True,
                    "created_at": SEEDED_AT,
                }
            )
    return documents
