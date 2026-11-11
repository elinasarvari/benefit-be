"""Completed history loaded into the stores (FR-003-22, ADR-003-06, T018).

Checks the documents `seed` inserts, built from the same dataset, so no database is needed.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from functools import cache
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

import pytest

from db.seeds.generate import generate_files
from db.seeds.history import History, build_history
from db.seeds.households import SeededDataset, build_dataset
from db.seeds.ids import HOUSEHOLD_A, HOUSEHOLD_B, HOUSEHOLD_C
from db.seeds.records import to_document
from packages.domain.eligibility import DeterminationRecord
from packages.domain.explanation import ExplanationAudience
from packages.rules.explanations import explain_determination

ANCHOR = date(2026, 11, 10)


@cache
def _dataset() -> SeededDataset:
    return build_dataset(ANCHOR)


@cache
def _history() -> History:
    return build_history(_dataset())


def _by_case(name: str) -> dict[UUID, list[dict[str, Any]]]:
    grouped: dict[UUID, list[dict[str, Any]]] = {}
    for document in _history().documents[name]:
        grouped.setdefault(document["case_id"], []).append(document)
    return grouped


def test_FR_003_22_history_has_decided_2025_and_current_period_cases_with_all_records() -> None:
    documents = _history().documents
    cases = documents["cases"]
    events, snapshots = _by_case("case_events"), _by_case("household_snapshots")
    determinations, outbox = _by_case("determinations"), _by_case("outbox")
    coverage, tasks, actions = (
        _by_case("coverage_periods"),
        _by_case("review_tasks"),
        _by_case("caseworker_actions"),
    )

    assert {c["household_id"] for c in cases}.isdisjoint({HOUSEHOLD_A, HOUSEHOLD_B, HOUSEHOLD_C})
    prior = [c for c in cases if c["submission_reference"].endswith("-prior")]
    current = [c for c in cases if c["submission_reference"].endswith("-current")]
    assert prior
    assert len(prior) == len(current)
    assert all(c["received_on"].year == 2025 for c in prior)
    assert all(c["received_on"].year in (2025, 2026) for c in current)
    for case in cases:
        case_id = case["_id"]
        assert case["state"] == "decided"
        assert case["open"] is False
        assert case["decided_on"] is not None
        assert case["decided_on"].date() <= ANCHOR
        ordered = sorted(events[case_id], key=lambda e: e["seq"])
        assert [e["seq"] for e in ordered] == list(range(1, len(ordered) + 1))
        assert case["last_seq"] == len(ordered)
        [snapshot] = snapshots[case_id]
        [determination] = determinations[case_id]
        as_of = snapshot["as_of_date"].date()
        assert determination["_id"] == uuid5(case_id, f"determination:{as_of.isoformat()}")
        assert snapshot["_id"] == uuid5(case_id, f"snapshot:{as_of.isoformat()}")
        assert determination["snapshot_id"] == snapshot["_id"]
        result = determination["determination"]["result"]
        assert len(determination["determination"]["rule_results"]) == 6
        [notice] = outbox[case_id]
        approved = case["outcome"] == "approved"
        assert notice["notice_type"] == ("approval" if approved else "denial")
        assert (case_id in coverage) is approved
        if result == "eligible":
            assert case_id not in tasks
            assert case_id not in actions
        else:
            [task] = tasks[case_id]
            [action] = actions[case_id]
            assert task["status"] == "closed"
            assert task["closed_by_action_id"] == action["action_id"]
            assert action["task_id"] == task["task_id"]
            assert action["outcome"] == "applied"
            assert (action["action"] == "override_to_approve") is approved
            assert (case["override"] is not None) is approved
            assert case["outcome"] in ("approved", "denied")
    results = {d["determination"]["result"] for d in documents["determinations"]}
    assert results == {"eligible", "ineligible", "cannot_determine"}
    assert {c["outcome"] for c in cases} == {"approved", "denied"}


def test_FR_003_22_history_files_reference_existing_determinations_and_cases(
    tmp_path: Path,
) -> None:
    generate_files(_dataset(), _history(), tmp_path)
    documents = _history().documents
    determination_ids = {d["_id"] for d in documents["determinations"]}
    case_ids = {c["_id"] for c in documents["cases"]}
    outbox = {n["case_id"]: n["notice_type"] for n in documents["outbox"]}

    notices = sorted((tmp_path / "history" / "notices").glob("*.json"))

    assert not (tmp_path / "history" / "explanations").exists()
    assert {d["determination_id"] for d in documents["decision_explanations"]} == determination_ids
    assert {UUID(p.name.split(".")[0]) for p in notices} == case_ids
    for path in notices:
        case_id_text, notice_type, suffix = path.name.split(".")
        notice = json.loads(path.read_text())
        assert suffix == "json"
        assert UUID(case_id_text) in case_ids
        assert notice["case_id"] == case_id_text
        assert notice["notice_type"] == notice_type == outbox[UUID(case_id_text)]
        assert notice["synthetic"] is True


@pytest.mark.parametrize("anchor", [ANCHOR, date(2026, 12, 1)])
def test_FR_003_22_history_is_a_function_of_seed_and_anchor(anchor: date) -> None:
    first = build_history(build_dataset(anchor))
    second = build_history(build_dataset(anchor))

    assert first.documents == second.documents
    assert first.counts()["cases"] > 0


def _record(determination: dict[str, Any]) -> DeterminationRecord:
    fields = {k: v for k, v in determination.items() if k not in ("_id", "synthetic")}
    return DeterminationRecord.model_validate({**fields, "determination_id": determination["_id"]})


def _stored(determination_id: UUID) -> dict[ExplanationAudience, dict[str, Any]]:
    return {
        ExplanationAudience(d["audience"]): d
        for d in _history().documents["decision_explanations"]
        if d["determination_id"] == determination_id
    }


def test_FR_004_13_03_seed_stores_two_synthetic_explanations_equal_to_explain_function_per_determination() -> (  # noqa: E501
    None
):
    determinations = _history().documents["determinations"]

    assert len(_history().documents["decision_explanations"]) == 2 * len(determinations)
    for determination in determinations:
        record = _record(determination)
        stored = _stored(determination["_id"])
        assert set(stored) == set(ExplanationAudience)
        for audience, document in stored.items():
            expected = to_document(explain_determination(record, audience)) | {"synthetic": True}
            assert document["_id"] == uuid5(determination["_id"], audience.value)
            assert document["synthetic"] is True
            assert {k: v for k, v in document.items() if k != "_id"} == expected
            assert document["built_at"] == determination["recorded_at"]


def test_FR_004_13_05_seeded_explanation_under_earlier_rule_set_keeps_its_manual_version_date() -> (  # noqa: E501
    None
):
    earlier = [
        d
        for d in _history().documents["determinations"]
        if d["determination"]["rule_set_id"] == "IL-ADULT-2025"
    ]

    assert earlier
    for determination in earlier:
        for document in _stored(determination["_id"]).values():
            assert document["citations"] or document["audience"] == "applicant"
            for citation in document["citations"]:
                assert citation["manual_version"] == datetime(2026, 1, 1, tzinfo=UTC)
