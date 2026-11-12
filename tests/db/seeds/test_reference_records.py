"""Reference records seeds (002, S-01; FR-002-22, KD-13, BA-11)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from functools import cache
from typing import Any

from db.seeds.households import SeededDataset, build_dataset
from db.seeds.ids import HOUSEHOLD_A, HOUSEHOLD_B, HOUSEHOLD_C
from db.seeds.records import REFERENCE_TAGS, reference_record_documents, reference_tag_households

ANCHOR = date(2026, 11, 10)


@cache
def _dataset() -> SeededDataset:
    return build_dataset(ANCHOR)


def _records() -> list[dict[str, Any]]:
    return reference_record_documents(_dataset())


def test_FR_002_22_seed_records_deterministic_and_synthetic() -> None:
    first = reference_record_documents(build_dataset(ANCHOR))
    second = reference_record_documents(build_dataset(ANCHOR))

    assert first == second
    assert first
    assert all(r["synthetic"] is True for r in first)
    assert len({r["_id"] for r in first}) == len(first)


def test_FR_002_22_reference_records_cover_every_member() -> None:
    members = {
        (h.household_id, m.member_id)
        for h in _dataset().households
        for year in h.years.values()
        for m in year.members
    }

    records = _records()

    assert {(r["household_id"], r["member_id"]) for r in records} == members
    assert len(records) == len(members)


def test_FR_002_22_each_tag_marks_the_planned_number_of_households() -> None:
    tagged = reference_tag_households(_dataset())
    by_tag: dict[str, set[Any]] = {}
    for record in _records():
        for tag in record["scenario_tags"]:
            by_tag.setdefault(tag, set()).add(record["household_id"])

    assert {tag: len(ids) for tag, ids in by_tag.items()} == REFERENCE_TAGS
    assert {tag: set(ids) for tag, ids in tagged.items()} == by_tag
    all_ids = [i for ids in tagged.values() for i in ids]
    assert len(all_ids) == len(set(all_ids))  # a household carries one tag
    assert not {HOUSEHOLD_A, HOUSEHOLD_B, HOUSEHOLD_C} & set(all_ids)  # demo stays exact


def _member(household_id: Any, index: int = 0) -> Any:
    household = next(h for h in _dataset().households if h.household_id == household_id)
    return household.years[2026].members[index]


def _record(household_id: Any, index: int = 0) -> dict[str, Any]:
    member = _member(household_id, index)
    return next(r for r in _records() if r["member_id"] == member.member_id)


def test_FR_002_22_identity_mismatch_households_have_birth_date_off_by_one_year() -> None:
    for household_id in reference_tag_households(_dataset())["reference-identity-mismatch"]:
        record = _record(household_id)
        member = _member(household_id)
        assert record["date_of_birth"].date() == member.date_of_birth + timedelta(days=365)
        assert record["given_name"] == member.given_name


def test_FR_002_22_income_differs_households_change_one_job_by_15_to_30_percent() -> None:
    untagged = {r["member_id"]: r for r in _records() if not r["scenario_tags"]}
    for household_id in reference_tag_households(_dataset())["reference-income-differs"]:
        changed = _record(household_id)
        member = _member(household_id)
        assert changed["date_of_birth"].date() == member.date_of_birth
        documented = _documented_monthly(household_id)
        reference = Decimal(changed["jobs"][0]["monthly_gross"])
        ratio = abs(reference - documented) / documented
        assert Decimal("0.149") <= ratio <= Decimal("0.301"), ratio
        assert [j["employer"] for j in changed["jobs"]] == [j.employer for j in member.jobs]
    assert untagged


def _documented_monthly(household_id: Any) -> Decimal:
    from db.seeds.records import _reference_jobs  # the untagged default for this member

    household = next(h for h in _dataset().households if h.household_id == household_id)
    year = household.years[2026]
    return Decimal(_reference_jobs(year, year.members[0])[0]["monthly_gross"])


def test_FR_002_22_undeclared_job_households_have_one_extra_reference_job() -> None:
    for household_id in reference_tag_households(_dataset())["reference-undeclared-job"]:
        record = _record(household_id)
        member = _member(household_id)
        assert len(record["jobs"]) == len(member.jobs) + 1
        assert record["jobs"][-1]["employer"] not in {j.employer for j in member.jobs}


def test_FR_002_22_job_missing_households_drop_one_declared_job_from_reference() -> None:
    for household_id in reference_tag_households(_dataset())["reference-job-missing"]:
        record = _record(household_id)
        member = _member(household_id)
        assert len(record["jobs"]) == len(member.jobs) - 1


def test_FR_002_22_untagged_records_hold_documented_income_as_money_strings() -> None:
    for record in _records():
        if record["scenario_tags"]:
            continue
        for job in record["jobs"]:
            assert isinstance(job["monthly_gross"], str)
            assert Decimal(job["monthly_gross"]) > 0
