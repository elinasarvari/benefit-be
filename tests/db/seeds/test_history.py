"""Completed history loaded into the stores (FR-003-22, ADR-003-06, T018).

Checks the documents `seed` inserts, built from the same dataset, so no database is needed.
"""

from __future__ import annotations

import json
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

import pytest

from db.seeds.generate import generate_files
from db.seeds.history import History, build_history
from db.seeds.households import SeededDataset, build_dataset
from db.seeds.ids import HOUSEHOLD_A, HOUSEHOLD_B, HOUSEHOLD_C

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

    explanations = sorted((tmp_path / "history" / "explanations").glob("*.json"))
    notices = sorted((tmp_path / "history" / "notices").glob("*.json"))

    assert {UUID(p.stem) for p in explanations} == determination_ids
    for path in explanations:
        explanation = json.loads(path.read_text())
        assert explanation["determination_id"] == path.stem
        assert UUID(explanation["case_id"]) in case_ids
        assert explanation["synthetic"] is True
        assert explanation["citations"]
        assert len(explanation["rule_ids"]) == 6
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
