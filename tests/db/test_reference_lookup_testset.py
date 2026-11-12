"""The S-02 reference lookup test set (002; FR-002-22, SC-002-02)."""

from __future__ import annotations

import json
from datetime import date
from functools import cache
from typing import Any

import pytest

from db.seeds.generate import generate_files
from db.seeds.history import History, build_history
from db.seeds.households import SeededDataset, build_dataset
from db.seeds.records import reference_record_documents
from db.seeds.test_sets import answer_lookup
from packages.domain.verification import IdentityMatch, ReferenceAnswer, ReferenceLookup

ANCHOR = date(2026, 11, 10)


@cache
def _dataset() -> SeededDataset:
    return build_dataset(ANCHOR)


@cache
def _history() -> History:
    return build_history(_dataset())


@pytest.fixture(scope="module")
def items(tmp_path_factory: pytest.TempPathFactory) -> list[dict[str, Any]]:
    out = tmp_path_factory.mktemp("generated")
    generate_files(_dataset(), _history(), out)
    payload = json.loads((out / "testsets" / "reference_lookup.json").read_text())
    assert payload["synthetic"] is True
    result: list[dict[str, Any]] = payload["items"]
    return result


def test_FR_002_22_reference_lookup_over_test_set(items: list[dict[str, Any]]) -> None:
    records = reference_record_documents(_dataset())
    lookups = [pair for item in items for pair in item["lookups"]]

    assert lookups
    for pair in lookups:
        lookup = ReferenceLookup.model_validate(pair["lookup"])
        expected = ReferenceAnswer.model_validate(pair["expected"])
        got = ReferenceAnswer.model_validate(
            answer_lookup(records, json.loads(lookup.model_dump_json()))
        )
        assert got == expected
        # exact on case-folded names: upper-casing the names changes nothing
        shouted = {
            **pair["lookup"],
            "given_name": pair["lookup"]["given_name"].upper(),
            "family_name": pair["lookup"]["family_name"].upper(),
        }
        assert ReferenceAnswer.model_validate(answer_lookup(records, shouted)) == expected


def test_FR_002_22_every_seeded_person_and_job_answers_as_seeded() -> None:
    records = reference_record_documents(_dataset())
    for household in _dataset().households:
        for member in household.years[2026].members:
            record = next(r for r in records if r["member_id"] == member.member_id)
            lookup = {
                "household_id": str(household.household_id),
                "member_id": str(member.member_id),
                "given_name": member.given_name,
                "family_name": member.family_name,
                "date_of_birth": member.date_of_birth.isoformat(),
            }
            answer = ReferenceAnswer.model_validate(answer_lookup(records, lookup))
            if "reference-identity-mismatch" in record["scenario_tags"]:
                assert answer.identity is IdentityMatch.no_match
                assert answer.jobs == []
            else:
                assert answer.identity is IdentityMatch.match
                assert [(j.employer, str(j.monthly_gross)) for j in answer.jobs] == [
                    (j["employer"], j["monthly_gross"]) for j in record["jobs"]
                ]


def test_FR_002_22_test_set_covers_every_scenario(items: list[dict[str, Any]]) -> None:
    scenarios = {s for item in items for s in item["scenarios"]}

    assert scenarios >= {
        "reference-identity-mismatch",
        "reference-income-differs",
        "reference-undeclared-job",
        "reference-job-missing",
        "mismatch",
        "zero-income",
        "missing-document",
        "no-record",
    }
    no_record = next(i for i in items if i["scenarios"] == ["no-record"])
    assert no_record["lookups"][0]["expected"]["identity"] == "no_record"
