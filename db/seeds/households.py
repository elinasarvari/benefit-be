"""200 synthetic households over two coverage years, with exact quotas (R8, FR-003-10/11/14/16).

Everything is a function of `(SEED, anchor)`: counts come from shuffled fixed quotas, never from
independent draws, and every date is an offset from the anchor date (ADR-003-04). All names and
employers are made up (PR-3).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from db.seeds.ids import HOUSEHOLD_A, HOUSEHOLD_B, HOUSEHOLD_C, rng, seeded_id
from db.seeds.names import EMPLOYERS, FAMILY_NAMES, GIVEN_NAMES, OTHER_STATES
from packages.domain.case import PayFrequency
from packages.domain.household import (
    HouseholdMember,
    HouseholdSnapshot,
    Job,
    JobIncome,
    MemberFacts,
    PayStubFact,
)
from packages.rules.income import monthly_income_from_stub, monthly_limit
from packages.rules.rule_sets import rule_set_for

YEARS = (2025, 2026)
HOUSEHOLD_COUNT = 200
CENT = Decimal("0.01")
PERIODS = {PayFrequency.weekly: 52, PayFrequency.biweekly: 26, PayFrequency.monthly: 12}
POOR_QUALITY_SHARE = Decimal("0.10")
CAPTURED_AT = datetime(2026, 1, 1, tzinfo=UTC)

_KINDS = ["zero"] * 10 + ["missing"] * 10 + ["under"] * 108 + ["over"] * 70
_SIZES = [1] * 50 + [2] * 50 + [3] * 40 + [4] * 30 + [5] * 20 + [6] * 10


class StubDocument(BaseModel):
    """A documented pay stub: the facts the stub PDF or image shows."""

    model_config = ConfigDict(frozen=True)

    document_id: UUID
    household_id: UUID
    member_id: UUID
    job_id: UUID
    coverage_year: int
    employee_name: str
    employer: str
    frequency: PayFrequency
    pay_date: date
    gross_pay: Decimal
    poor_quality: bool = False


@dataclass(frozen=True)
class HouseholdYear:
    coverage_year: int
    members: list[HouseholdMember]
    stubs: list[StubDocument]


@dataclass(frozen=True)
class SeededHousehold:
    household_id: UUID
    seed_key: str
    scenarios: frozenset[str]
    address_state: str
    years: dict[int, HouseholdYear] = field(default_factory=dict)


@dataclass(frozen=True)
class SeededDataset:
    anchor: date
    households: list[SeededHousehold]

    def stubs(self) -> list[StubDocument]:
        return [s for h in self.households for y in YEARS for s in h.years[y].stubs]


@dataclass(frozen=True)
class _Plan:
    index: int
    kind: str
    size: dict[int, int]
    mismatch: bool
    jobs: int
    out_of_state: bool
    medicare: bool
    scenarios: frozenset[str]
    household_id: UUID
    seed_key: str


def _plan(quota: random.Random) -> list[_Plan]:
    kinds = list(_KINDS)
    quota.shuffle(kinds)
    # A is under the limit (eligible), B over it (ineligible), C is the first "missing" household
    # (declared job without a usable stub, cannot determine), FR-003-14.
    kinds = ["under", "over", *kinds]
    sizes = list(_SIZES)
    sizes.remove(2)
    sizes.remove(3)
    quota.shuffle(sizes)
    sizes = [2, 3, *sizes]

    indices = range(HOUSEHOLD_COUNT)
    earning = [i for i in indices if i >= 2 and kinds[i] in ("under", "over")]
    mismatch = {1, *quota.sample([i for i in earning], 29)}
    multi = set(quota.sample(earning, 20))
    changing = set(quota.sample(range(2, HOUSEHOLD_COUNT), 20))
    abroad = set(quota.sample(range(2, HOUSEHOLD_COUNT), 6))
    medicare = set(quota.sample(range(2, HOUSEHOLD_COUNT), 6))

    named: dict[int, str] = {0: "a", 1: "b"}
    # 007's demo households: eligible on held data, no mismatch, one job (E) or several (F).
    renewable = [
        i
        for i in indices
        if i >= 2 and kinds[i] == "under" and i not in mismatch | changing | abroad | medicare
    ]
    renewal_labels: dict[int, str] = {}
    for tag, pool in (
        ("renewal-automatic", [i for i in renewable if i not in multi]),
        ("renewal-one-document", [i for i in renewable if i in multi]),
    ):
        renewal_labels[next(i for i in pool if i not in renewal_labels)] = tag
    for label, pool in (
        ("zero-income", [i for i in indices if kinds[i] == "zero"]),
        ("multi-job", sorted(multi)),
        ("missing-document", [i for i in indices if kinds[i] == "missing"]),
        ("size-change", sorted(changing)),
    ):
        pick = next((i for i in pool if i not in named and i not in renewal_labels), None)
        if pick is not None:
            named[pick] = label

    plans = []
    for i in indices:
        size26 = sizes[i]
        size25 = size26
        if i in changing:
            size25 = 2 if size26 == 1 else 5 if size26 == 6 else size26 + (-1 if i % 2 else 1)
        tags = {
            tag
            for tag, members in (
                ("mismatch", mismatch),
                ("multi-job", multi),
                ("size-change", changing),
                ("out-of-state", abroad),
            )
            if i in members
        }
        if kinds[i] == "zero":
            tags.add("zero-income")
        if kinds[i] == "missing":
            tags.add("missing-document")
        if i == 0:
            tags.add("new-application")
        if i == 1:
            tags.add("renewal-due")
        if i in renewal_labels:
            tags.add(renewal_labels[i])
        named_label = named.get(i)
        key: str
        if i == 0:
            household_id, key = HOUSEHOLD_A, "household-a"
        elif i == 1:
            household_id, key = HOUSEHOLD_B, "household-b"
        elif named_label == "missing-document":
            # main's demo household C (applicant3, cannot-determine); ADR-003-07
            household_id, key = HOUSEHOLD_C, "household-c"
        elif i in renewal_labels:
            key = f"household-{renewal_labels[i]}"
            household_id = seeded_id(key)
        elif named_label:
            household_id, key = seeded_id(f"household-{named_label}"), f"household-{named_label}"
        else:
            household_id, key = seeded_id(f"household-{i:04d}"), f"household-{i:04d}"
        plans.append(
            _Plan(
                index=i,
                kind=kinds[i],
                size={2025: size25, 2026: size26},
                mismatch=i in mismatch,
                jobs=2 + (i % 2) if i in multi else 1,
                out_of_state=i in abroad,
                medicare=i in medicare,
                scenarios=frozenset(tags),
                household_id=household_id,
                seed_key=key,
            )
        )
    return plans


@dataclass(frozen=True)
class _Person:
    given: str
    family: str
    date_of_birth: date
    relationship: str


def _people(plan: _Plan, anchor: date, draw: random.Random) -> list[_Person]:
    family = draw.choice(FAMILY_NAMES)
    people = []
    for k in range(max(plan.size.values())):
        if k == 0:
            years, relationship = draw.randint(22, 60), "self"
        elif k == 1:
            years, relationship = draw.randint(22, 60), "spouse"
        else:
            years, relationship = draw.randint(1, 17), "child"
        extra = draw.randint(0, 364)
        people.append(
            _Person(
                given=draw.choice(GIVEN_NAMES),
                family=family,
                date_of_birth=anchor - timedelta(days=years * 365 + extra),
                relationship=relationship,
            )
        )
    return people


def _split(total: Decimal, count: int, draw: random.Random) -> list[Decimal]:
    weights = [Decimal(str(draw.uniform(0.5, 1.5))) for _ in range(count)]
    return [total * w / sum(weights) for w in weights]


def _ratio(plan: _Plan, year: int, draw: random.Random) -> Decimal:
    if plan.kind == "under" or (plan.kind == "missing" and year == 2026):
        return Decimal(str(draw.uniform(0.30, 0.90)))
    return Decimal(str(draw.uniform(1.10, 1.80)))


def _year(
    plan: _Plan, year: int, anchor: date, people: list[_Person], draw: random.Random
) -> HouseholdYear:
    size = plan.size[year]
    rule_set = rule_set_for(date(year, 6, 1))
    if rule_set is None:
        raise ValueError(f"no rule set for {year}")
    limit = monthly_limit(rule_set, size)
    weekly_a = plan.index == 0
    members_jobs: dict[int, list[Job]] = {k: [] for k in range(size)}
    stubs: list[StubDocument] = []
    pay_offset = 0 if year == 2026 else 365
    if plan.kind != "zero":
        target = limit * (Decimal("0.80") if weekly_a else _ratio(plan, year, draw))
        for j, monthly in enumerate(_split(target, plan.jobs, draw)):
            holder = 1 if (j % 2 == 1 and size >= 2) else 0
            frequency = (
                PayFrequency.weekly
                if weekly_a
                else draw.choices(list(PERIODS), weights=[40, 35, 25])[0]
            )
            gross = (monthly * 12 / PERIODS[frequency]).quantize(CENT, rounding=ROUND_HALF_UP)
            declared = gross
            if plan.mismatch and j == 0:
                factor = Decimal(str(draw.uniform(0.10, 0.14)))
                sign = 1 if draw.random() < 0.5 else -1
                declared = (gross * (1 + sign * factor)).quantize(CENT, rounding=ROUND_HALF_UP)
            employer = draw.choice(EMPLOYERS)
            job_id = seeded_id(f"job-{plan.seed_key}-{year}-{j}")
            members_jobs[holder].append(
                Job(
                    job_id=job_id,
                    employer=employer,
                    declared_frequency=frequency,
                    declared_gross=declared,
                )
            )
            pay_date = anchor - timedelta(days=pay_offset + draw.randint(3, 40))
            if plan.kind == "missing" and year == 2026:
                continue  # declared job whose pay stub was never supplied
            person = people[holder]
            stubs.append(
                StubDocument(
                    document_id=seeded_id(f"document-{plan.seed_key}-{year}-{j}"),
                    household_id=plan.household_id,
                    member_id=seeded_id(f"member-{plan.seed_key}-{year}-{holder}"),
                    job_id=job_id,
                    coverage_year=year,
                    employee_name=f"{person.given} {person.family}",
                    employer=employer,
                    frequency=frequency,
                    pay_date=pay_date,
                    gross_pay=gross,
                )
            )
    state = OTHER_STATES[plan.index % len(OTHER_STATES)] if plan.out_of_state else "IL"
    members = [
        HouseholdMember(
            member_id=seeded_id(f"member-{plan.seed_key}-{year}-{k}"),
            household_id=plan.household_id,
            given_name=people[k].given,
            family_name=people[k].family,
            date_of_birth=people[k].date_of_birth,
            relationship=people[k].relationship,
            address_state=state,
            citizenship_attested=True,
            medicare_entitled=plan.medicare and k == 0,
            declared_zero_income=not members_jobs[k],
            jobs=members_jobs[k],
            coverage_year=year,
        )
        for k in range(size)
    ]
    return HouseholdYear(coverage_year=year, members=members, stubs=stubs)


def build_dataset(anchor: date) -> SeededDataset:
    plans = _plan(rng("households"))
    households = []
    for plan in plans:
        draw = rng(f"household:{plan.index}")
        people = _people(plan, anchor, draw)
        years = {year: _year(plan, year, anchor, people, draw) for year in YEARS}
        households.append(
            SeededHousehold(
                household_id=plan.household_id,
                seed_key=plan.seed_key,
                scenarios=plan.scenarios,
                address_state=years[2026].members[0].address_state,
                years=years,
            )
        )
    return SeededDataset(anchor=anchor, households=_mark_poor_quality(households, anchor))


def _mark_poor_quality(households: list[SeededHousehold], anchor: date) -> list[SeededHousehold]:
    """Exactly 10% of stubs are poor quality (FR-003-12); demo households stay clean."""
    candidates = [s.document_id for h in households[2:] for y in YEARS for s in h.years[y].stubs]
    total = sum(len(h.years[y].stubs) for h in households for y in YEARS)
    count = int((POOR_QUALITY_SHARE * total).to_integral_value(rounding=ROUND_HALF_UP))
    chosen = set(rng("documents:poor-quality").sample(candidates, count))
    return [
        replace(
            h,
            years={
                y: replace(
                    h.years[y],
                    stubs=[
                        s.model_copy(update={"poor_quality": s.document_id in chosen})
                        for s in h.years[y].stubs
                    ],
                )
                for y in YEARS
            },
        )
        for h in households
    ]


def snapshot_for(
    household: SeededHousehold,
    year: int,
    *,
    case_id: UUID | None = None,
    captured_at: datetime = CAPTURED_AT,
    as_of_date: date | None = None,
) -> HouseholdSnapshot:
    """The engine's view of one household-year: stored members plus their documented stubs."""
    data = household.years[year]
    stubs_by_job: dict[UUID, list[PayStubFact]] = {}
    for stub in data.stubs:
        stubs_by_job.setdefault(stub.job_id, []).append(
            PayStubFact(
                document_id=stub.document_id,
                pay_date=stub.pay_date,
                frequency=stub.frequency,
                gross_pay=stub.gross_pay,
            )
        )
    members = [
        MemberFacts(
            member_id=m.member_id,
            relationship=m.relationship,
            date_of_birth=m.date_of_birth,
            address_state=m.address_state,
            citizenship_attested=m.citizenship_attested,
            medicare_entitled=m.medicare_entitled,
            identity_verified=True,
            declared_zero_income=m.declared_zero_income,
            jobs=[JobIncome(job_id=j.job_id, stubs=stubs_by_job.get(j.job_id, [])) for j in m.jobs],
        )
        for m in data.members
    ]
    return HouseholdSnapshot(
        snapshot_id=seeded_id(f"snapshot-{household.seed_key}-{year}"),
        household_id=household.household_id,
        case_id=case_id or seeded_id(f"case-{household.seed_key}-{year}"),
        as_of_date=as_of_date or date(year, 6, 1),
        captured_at=captured_at,
        members=members,
    )


__all__ = [
    "HouseholdYear",
    "SeededDataset",
    "SeededHousehold",
    "StubDocument",
    "build_dataset",
    "monthly_income_from_stub",
    "snapshot_for",
]
