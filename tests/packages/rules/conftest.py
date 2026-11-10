"""Synthetic snapshot builders and hypothesis strategies for the rules tests (PR-3: no names)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from hypothesis import strategies as st

from packages.domain.household import (
    HouseholdSnapshot,
    JobIncome,
    MemberFacts,
    PayFrequency,
    PayStubFact,
)

NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://benefit-case-platform.example/rules-tests")
AS_OF = date(2026, 6, 1)
SELF_DOB = date(1996, 1, 1)  # 30 on AS_OF


def uid(name: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, name)


def stub(
    frequency: PayFrequency | str = PayFrequency.weekly,
    gross: str | Decimal = "300",
    pay_date: date = date(2026, 5, 29),
    name: str = "stub",
) -> PayStubFact:
    return PayStubFact(
        document_id=uid(f"{name}-{pay_date}-{gross}"),
        pay_date=pay_date,
        frequency=PayFrequency(frequency),
        gross_pay=Decimal(gross),
    )


def job(name: str = "job", stubs: list[PayStubFact] | None = None) -> JobIncome:
    return JobIncome(job_id=uid(name), stubs=[stub()] if stubs is None else stubs)


def member(
    name: str = "self",
    *,
    relationship: str = "self",
    date_of_birth: date = SELF_DOB,
    address_state: str = "IL",
    citizenship_attested: bool | None = True,
    medicare_entitled: bool | None = False,
    identity_verified: bool | None = True,
    declared_zero_income: bool = False,
    jobs: list[JobIncome] | None = None,
) -> MemberFacts:
    return MemberFacts(
        member_id=uid(f"member-{name}"),
        relationship=relationship,  # type: ignore[arg-type]
        date_of_birth=date_of_birth,
        address_state=address_state,
        citizenship_attested=citizenship_attested,
        medicare_entitled=medicare_entitled,
        identity_verified=identity_verified,
        declared_zero_income=declared_zero_income,
        jobs=[job(f"{name}-job")] if jobs is None else jobs,
    )


def snapshot(
    members: list[MemberFacts] | None = None,
    *,
    captured_at: datetime = datetime(2026, 5, 30, 12, 0, tzinfo=UTC),
) -> HouseholdSnapshot:
    return HouseholdSnapshot(
        snapshot_id=uid("snapshot"),
        household_id=uid("household"),
        case_id=uid("case"),
        captured_at=captured_at,
        members=[member()] if members is None else members,
    )


def eligible_snapshot() -> HouseholdSnapshot:
    return snapshot([member()])


# hypothesis strategies -----------------------------------------------------------------------

frequencies = st.sampled_from(list(PayFrequency))
tri_state = st.sampled_from([True, False, None])
dates_1940_2026 = st.dates(min_value=date(1940, 1, 1), max_value=date(2026, 12, 31))
as_of_dates = st.dates(min_value=date(2024, 1, 1), max_value=date(2027, 12, 31))
money = st.decimals(min_value=Decimal(0), max_value=Decimal("100000.00"), places=2)


@st.composite
def stubs(draw: st.DrawFn) -> PayStubFact:
    return PayStubFact(
        document_id=draw(st.uuids(version=4)),
        pay_date=draw(st.dates(min_value=date(2025, 1, 1), max_value=date(2026, 12, 31))),
        frequency=draw(frequencies),
        gross_pay=draw(st.decimals(min_value=0, max_value=Decimal("5000.00"), places=2)),
    )


@st.composite
def members(draw: st.DrawFn, relationship: str = "self") -> MemberFacts:
    jobs = [
        JobIncome(job_id=draw(st.uuids(version=4)), stubs=draw(st.lists(stubs(), max_size=2)))
        for _ in range(draw(st.integers(0, 2)))
    ]
    return MemberFacts(
        member_id=draw(st.uuids(version=4)),
        relationship=relationship,  # type: ignore[arg-type]
        date_of_birth=draw(dates_1940_2026),
        address_state=draw(st.sampled_from(["IL", "WI", "IN"])),
        citizenship_attested=draw(tri_state),
        medicare_entitled=draw(tri_state),
        identity_verified=draw(tri_state),
        declared_zero_income=draw(st.booleans()),
        jobs=jobs,
    )


@st.composite
def snapshots(draw: st.DrawFn) -> HouseholdSnapshot:
    people = [draw(members("self"))] if draw(st.integers(0, 9)) else []
    people += [draw(members("spouse")) for _ in range(draw(st.integers(0, 3)))]
    return HouseholdSnapshot(
        snapshot_id=draw(st.uuids(version=4)),
        household_id=draw(st.uuids(version=4)),
        case_id=draw(st.uuids(version=4)),
        captured_at=datetime(2026, 5, 30, tzinfo=UTC),
        members=people,
    )


def years_before(day: date, years: int) -> date:
    """Same month/day `years` earlier (Feb 29 → Feb 28), for boundary birthdays."""
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return (day - timedelta(days=1)).replace(year=day.year - years)
