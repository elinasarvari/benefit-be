"""The check over the seeded data set (002; SC-002-02, ADR-002-05)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from db.seeds.generate import generate_files
from db.seeds.history import build_history
from db.seeds.households import SeededDataset, build_dataset
from packages.domain.household import PayStubFact
from packages.domain.verification import FindingCause, PayStubDetails, ReferenceAnswer
from packages.rules.income import monthly_income_from_stub
from packages.rules.verification import CheckInput, DeclaredJob, DeclaredMember, check_pay_stubs

ANCHOR = date(2026, 11, 10)
THRESHOLD = Decimal(10)
CENT = Decimal("0.01")


@cache
def _dataset() -> SeededDataset:
    return build_dataset(ANCHOR)


@pytest.fixture(scope="module")
def answers(tmp_path_factory: pytest.TempPathFactory) -> dict[UUID, ReferenceAnswer]:
    out: Path = tmp_path_factory.mktemp("generated")
    generate_files(_dataset(), build_history(_dataset()), out)
    items: list[dict[str, Any]] = json.loads(
        (out / "testsets" / "reference_lookup.json").read_text()
    )["items"]
    return {
        UUID(pair["expected"]["member_id"]): ReferenceAnswer.model_validate(pair["expected"])
        for item in items
        for pair in item["lookups"]
    }


def _monthly(frequency: Any, gross: Decimal) -> Decimal:
    fact = PayStubFact(
        document_id=UUID(int=1), pay_date=ANCHOR, frequency=frequency, gross_pay=gross
    )
    return monthly_income_from_stub(fact)


def test_SC_002_02_all_seeded_mismatches_flagged(answers: dict[UUID, ReferenceAnswer]) -> None:
    flagged_expected = 0
    for household in _dataset().households:
        year = household.years[2026]
        if not year.stubs:
            continue
        members = [
            DeclaredMember(
                m.member_id,
                f"{m.given_name} {m.family_name}",
                m.declared_zero_income,
                [
                    DeclaredJob(
                        j.job_id, j.employer, _monthly(j.declared_frequency, j.declared_gross)
                    )
                    for j in m.jobs
                ],
            )
            for m in year.members
        ]
        stubs = [
            PayStubDetails(
                document_id=s.document_id,
                case_id=UUID(int=2),
                household_id=household.household_id,
                employee_name=s.employee_name,
                employer=s.employer,
                frequency=s.frequency,
                gross_pay=s.gross_pay,
                pay_date=s.pay_date,
            )
            for s in year.stubs
        ]
        result = check_pay_stubs(
            CheckInput(
                case_id=UUID(int=2),
                as_of_date=ANCHOR,
                members=members,
                stubs=stubs,
                references=answers,
            ),
            threshold_percent=THRESHOLD,
        )
        flagged = {f.job_id for f in result.findings if f.cause is FindingCause.income_mismatch}
        expected = set()
        for member in year.members:
            for job in member.jobs:
                stub = next((s for s in year.stubs if s.job_id == job.job_id), None)
                if stub is None:
                    continue
                declared = _monthly(job.declared_frequency, job.declared_gross)
                documented = _monthly(stub.frequency, stub.gross_pay)
                if abs(documented - declared) * 100 > THRESHOLD * declared:
                    expected.add(job.job_id)
        assert flagged == expected, household.seed_key
        flagged_expected += len(expected)
        if "mismatch" not in household.scenarios:
            assert not flagged, household.seed_key
    assert flagged_expected >= 20  # the seed plants 30 mismatching households
