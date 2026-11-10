from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from packages.domain.household import (
    HouseholdMember,
    HouseholdSnapshot,
    Job,
    JobIncome,
    MemberFacts,
    PayFrequency,
    PayStubFact,
)

MEMBER_ID = UUID("00000000-0000-5000-8000-000000000001")
HOUSEHOLD_ID = UUID("00000000-0000-5000-8000-000000000002")
JOB_ID = UUID("00000000-0000-5000-8000-000000000003")
DOC_ID = UUID("00000000-0000-5000-8000-000000000004")


def _stub(gross: str = "400.00") -> PayStubFact:
    return PayStubFact(
        document_id=DOC_ID,
        pay_date=date(2026, 10, 30),
        frequency=PayFrequency.weekly,
        gross_pay=Decimal(gross),
    )


def _member_facts() -> MemberFacts:
    return MemberFacts(
        member_id=MEMBER_ID,
        relationship="self",
        date_of_birth=date(1990, 1, 1),
        address_state="IL",
        citizenship_attested=True,
        medicare_entitled=False,
        identity_verified=None,
        declared_zero_income=False,
        jobs=[JobIncome(job_id=JOB_ID, stubs=[_stub()])],
    )


def _snapshot() -> HouseholdSnapshot:
    return HouseholdSnapshot(
        snapshot_id=UUID("00000000-0000-5000-8000-000000000005"),
        household_id=HOUSEHOLD_ID,
        case_id=UUID("00000000-0000-5000-8000-000000000006"),
        captured_at=datetime(2026, 11, 10, tzinfo=UTC),
        members=[_member_facts()],
    )


def test_FR_003_19_household_snapshot_is_frozen_and_member_facts_have_no_names() -> None:
    snapshot = _snapshot()

    with pytest.raises(ValidationError):
        snapshot.case_id = HOUSEHOLD_ID  # type: ignore[misc]
    with pytest.raises(ValidationError):
        snapshot.members[0].address_state = "WI"  # type: ignore[misc]
    assert not {"given_name", "family_name"} & set(MemberFacts.model_fields)
    with pytest.raises(ValidationError):
        MemberFacts(**_member_facts().model_dump(), given_name="Sample")


def test_FR_003_16_stored_member_keeps_unanswered_facts_as_none() -> None:
    member = HouseholdMember(
        member_id=MEMBER_ID,
        household_id=HOUSEHOLD_ID,
        given_name="Sample",
        family_name="Person",
        date_of_birth=date(1990, 1, 1),
        relationship="self",
        address_state="IL",
        citizenship_attested=None,
        medicare_entitled=None,
        declared_zero_income=False,
        jobs=[
            Job(
                job_id=JOB_ID,
                employer="Sample Employer Inc.",
                declared_frequency=PayFrequency.biweekly,
                declared_gross=Decimal("800.00"),
            )
        ],
        coverage_year=2026,
    )

    assert member.citizenship_attested is None
    assert member.medicare_entitled is None
    with pytest.raises(ValidationError):
        HouseholdMember(**{**member.model_dump(), "relationship": "cousin"})
    with pytest.raises(ValidationError):
        HouseholdMember(**{**member.model_dump(), "address_state": "ILL"})


def test_FR_003_17_pay_stub_rejects_negative_gross_and_money_serialises_as_string() -> None:
    with pytest.raises(ValidationError):
        _stub("-1")

    dumped = _stub("1733.33").model_dump(mode="json")

    assert dumped["gross_pay"] == "1733.33"
    assert [f.value for f in PayFrequency] == ["weekly", "biweekly", "semimonthly", "monthly"]


def test_FR_003_17_job_income_allows_empty_stub_list() -> None:
    assert JobIncome(job_id=JOB_ID, stubs=[]).stubs == []


def test_FR_003_19_snapshot_round_trips_through_json() -> None:
    snapshot = _snapshot()

    assert HouseholdSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot
