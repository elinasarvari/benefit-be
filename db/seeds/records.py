"""Turn validated domain models into MongoDB documents (the shape the 0002 validators expect).

UUIDs stay UUIDs (standard binary), `date` becomes a UTC midnight BSON date, `Decimal` becomes a
string (ADR-003-02), enums become their values. A model's identity field becomes `_id`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from db.seeds.households import HouseholdYear, SeededDataset, StubDocument
from db.seeds.ids import HOUSEHOLD_A, HOUSEHOLD_B, HOUSEHOLD_C, HOUSEHOLD_D, rng, seeded_id
from db.seeds.names import EMPLOYERS
from packages.domain.case import PayFrequency
from packages.domain.household import HouseholdMember, PayStubFact
from packages.rules.income import monthly_income_from_stub


def _convert(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return {name: _convert(getattr(value, name)) for name in type(value).model_fields}
    if isinstance(value, dict):
        return {(k.value if isinstance(k, Enum) else k): _convert(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_convert(v) for v in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=UTC)
    return value


def convert(value: Any) -> Any:
    """A model, dict, list or value in its stored form: enums as values, money as strings,
    dates as UTC midnight datetimes."""
    return _convert(value)


def to_document(model: BaseModel, id_field: str | None = None) -> dict[str, Any]:
    document: dict[str, Any] = _convert(model)
    if id_field is not None:
        document["_id"] = document.pop(id_field)
    return document


# ---- reference records (002, FR-002-22; S-01) ----------------------------------------------

REFERENCE_TAGS: dict[str, int] = {
    "reference-identity-mismatch": 3,
    "reference-income-differs": 10,
    "reference-undeclared-job": 3,
    "reference-job-missing": 2,
}
_CENT = Decimal("0.01")


def _monthly(frequency: PayFrequency, gross: Decimal, document_id: UUID) -> Decimal:
    fact = PayStubFact(
        document_id=document_id, pay_date=date(2000, 1, 1), frequency=frequency, gross_pay=gross
    )
    return monthly_income_from_stub(fact)


def _reference_jobs(year: HouseholdYear, member: HouseholdMember) -> list[dict[str, Any]]:
    """Documented monthly income per job; a job with no stub falls back to its declared figure."""
    latest: dict[UUID, StubDocument] = {}
    for stub in year.stubs:
        if stub.job_id not in latest or stub.pay_date > latest[stub.job_id].pay_date:
            latest[stub.job_id] = stub
    jobs: list[dict[str, Any]] = []
    for job in member.jobs:
        documented = latest.get(job.job_id)
        monthly = (
            _monthly(documented.frequency, documented.gross_pay, documented.document_id)
            if documented is not None
            else _monthly(job.declared_frequency, job.declared_gross, job.job_id)
        )
        jobs.append({"employer": job.employer, "monthly_gross": monthly})
    return jobs


def reference_tag_households(dataset: SeededDataset) -> dict[str, list[UUID]]:
    """Which households carry each reference scenario tag (picked by the seeded draw).

    Households A, B, C and D stay exact; only households whose first member has a documented job
    are picked, so every tag changes something the check can see.
    """
    demo = {HOUSEHOLD_A, HOUSEHOLD_B, HOUSEHOLD_C, HOUSEHOLD_D}
    pool: list[UUID] = []
    for household in dataset.households:
        year = household.years[2026]
        holder = year.members[0]
        if household.household_id in demo or not holder.jobs:
            continue
        if any(s.job_id == holder.jobs[0].job_id for s in year.stubs):
            pool.append(household.household_id)
    picked = rng("reference-records:households").sample(pool, sum(REFERENCE_TAGS.values()))
    tagged: dict[str, list[UUID]] = {}
    start = 0
    for tag, count in REFERENCE_TAGS.items():
        tagged[tag] = picked[start : start + count]
        start += count
    return tagged


def reference_record_documents(dataset: SeededDataset) -> list[dict[str, Any]]:
    """One `reference_records` document per seeded member and year (S-01).

    Names and birth date are the member's; the reference jobs are the documented monthly income,
    changed on the first member of each tagged household in the current year (2026).
    """
    tag_of = {
        household_id: tag
        for tag, household_ids in reference_tag_households(dataset).items()
        for household_id in household_ids
    }
    draw = rng("reference-records:values")
    documents: list[dict[str, Any]] = []
    for household in dataset.households:
        for year in sorted(household.years):
            for index, member in enumerate(household.years[year].members):
                jobs = _reference_jobs(household.years[year], member)
                birth = member.date_of_birth
                tag = tag_of.get(household.household_id) if year == 2026 and index == 0 else None
                if tag == "reference-identity-mismatch":
                    birth = birth + timedelta(days=365)
                elif tag == "reference-income-differs":
                    factor = Decimal(str(draw.uniform(0.15, 0.30)))
                    sign = 1 if draw.random() < 0.5 else -1
                    changed = jobs[0]["monthly_gross"] * (1 + sign * factor)
                    jobs[0]["monthly_gross"] = changed.quantize(_CENT, rounding=ROUND_HALF_UP)
                elif tag == "reference-undeclared-job":
                    taken = {j["employer"] for j in jobs}
                    extra = Decimal(str(draw.uniform(600, 2000))).quantize(_CENT)
                    employer = draw.choice([e for e in EMPLOYERS if e not in taken])
                    jobs.append({"employer": employer, "monthly_gross": extra})
                elif tag == "reference-job-missing":
                    jobs = jobs[1:]
                documents.append(
                    _convert(
                        {
                            "_id": seeded_id(f"reference-{member.member_id}"),
                            "household_id": household.household_id,
                            "member_id": member.member_id,
                            "given_name": member.given_name,
                            "family_name": member.family_name,
                            "date_of_birth": birth,
                            "jobs": jobs,
                            "scenario_tags": [tag] if tag else [],
                            "synthetic": True,
                        }
                    )
                )
    return documents
