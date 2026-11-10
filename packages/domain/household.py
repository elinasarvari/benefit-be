from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Relationship = Literal["self", "spouse", "child", "other"]
StateCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]


class PayFrequency(StrEnum):
    weekly = "weekly"
    biweekly = "biweekly"
    semimonthly = "semimonthly"
    monthly = "monthly"


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    employer: str
    declared_frequency: PayFrequency
    declared_gross: Decimal = Field(ge=0)


class HouseholdMember(BaseModel):
    """Stored member for one coverage year. `None` on an attested fact means not answered."""

    model_config = ConfigDict(extra="forbid")

    member_id: UUID
    household_id: UUID
    given_name: str
    family_name: str
    date_of_birth: date
    relationship: Relationship
    address_state: StateCode
    citizenship_attested: bool | None
    medicare_entitled: bool | None
    declared_zero_income: bool
    jobs: list[Job]
    coverage_year: int


class PayStubFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: UUID
    pay_date: date
    frequency: PayFrequency
    gross_pay: Decimal = Field(ge=0)


class JobIncome(BaseModel):
    """An empty `stubs` list means income is missing for that job."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: UUID
    stubs: list[PayStubFact]


class MemberFacts(BaseModel):
    """What the rules engine sees about a member: no names."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    member_id: UUID
    relationship: Relationship
    date_of_birth: date
    address_state: StateCode
    citizenship_attested: bool | None
    medicare_entitled: bool | None
    identity_verified: bool | None
    declared_zero_income: bool
    jobs: list[JobIncome]


class HouseholdSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: UUID
    household_id: UUID
    case_id: UUID
    captured_at: datetime
    members: list[MemberFacts]
