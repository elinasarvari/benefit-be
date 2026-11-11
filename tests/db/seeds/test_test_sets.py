"""Generated test sets for 002, 007 and 008 and the manifest (FR-003-13, SC-003-03, T017).

These build the files into a temporary directory from the dataset and history the seeds use;
no database is needed, and what they check is what `seed` writes under `db/seeds/generated/`.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from db.seeds.generate import GENERATOR_VERSION, generate_files
from db.seeds.history import DEMO_WINDOW_DAYS, History, build_history
from db.seeds.households import SeededDataset, build_dataset
from db.seeds.ids import SEED

ANCHOR = date(2026, 11, 10)


@cache
def _dataset() -> SeededDataset:
    return build_dataset(ANCHOR)


@cache
def _history() -> History:
    return build_history(_dataset())


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, Any]]:
    out = tmp_path_factory.mktemp("generated")
    manifest = generate_files(_dataset(), _history(), out)
    return out, manifest


def _items(out: Path, name: str) -> list[dict[str, Any]]:
    payload = json.loads((out / "testsets" / f"{name}.json").read_text())
    assert payload["synthetic"] is True
    assert payload["seed"] == SEED
    assert payload["anchor_date"] == ANCHOR.isoformat()
    assert payload["generator_version"] == GENERATOR_VERSION
    items: list[dict[str, Any]] = payload["items"]
    return items


def test_FR_003_13_pay_stub_extractions_has_10_to_20_stubs_with_expected_values(
    generated: tuple[Path, dict[str, Any]],
) -> None:
    out, _ = generated
    items = _items(out, "pay_stub_extractions")
    stubs = {str(s.document_id): s for s in _dataset().stubs()}

    assert 10 <= len(items) <= 20
    assert len({i["document_id"] for i in items}) == len(items)
    for item in items:
        stub = stubs[item["document_id"]]
        assert (out / item["path"]).is_file()
        assert item["path"].endswith(".png" if stub.poor_quality else ".pdf")
        assert item["poor_quality"] is stub.poor_quality
        assert item["expected"] == {
            "employee_name": stub.employee_name,
            "employer": stub.employer,
            "frequency": stub.frequency.value,
            "pay_date": stub.pay_date.isoformat(),
            "gross_pay": str(stub.gross_pay),
        }
    assert {i["expected"]["frequency"] for i in items} >= {"weekly", "biweekly", "monthly"}
    assert any(i["poor_quality"] for i in items)
    assert any(not i["poor_quality"] for i in items)


def test_FR_003_13_redeterminations_due_lists_households_due_in_demo_window(
    generated: tuple[Path, dict[str, Any]],
) -> None:
    out, _ = generated
    items = _items(out, "redeterminations_due")
    periods = [
        p
        for p in _history().documents["coverage_periods"]
        if p["status"] == "active" and 0 <= (p["end_date"].date() - ANCHOR).days <= DEMO_WINDOW_DAYS
    ]

    assert items
    assert {i["coverage_period_id"] for i in items} == {str(p["_id"]) for p in periods}
    by_id = {str(p["_id"]): p for p in periods}
    for item in items:
        period = by_id[item["coverage_period_id"]]
        end = date.fromisoformat(item["coverage_end"])
        assert item["household_id"] == str(period["household_id"])
        assert end == period["end_date"].date()
        # FR-007-01: the redetermination starts 60 days before the coverage ends, so within the
        # next 30 days for every household the demo lists.
        assert date.fromisoformat(item["redetermination_start"]) == end - timedelta(days=60)
        assert 0 <= (date.fromisoformat(item["redetermination_start"]) - ANCHOR).days <= 30
        assert item["expected_path"] in ("renew_automatically", "ask_applicant")
    assert {i["expected_path"] for i in items} >= {"renew_automatically"}


def test_FR_003_13_expected_metrics_match_values_recomputed_from_seeded_history(
    generated: tuple[Path, dict[str, Any]],
) -> None:
    out, _ = generated
    items = _items(out, "expected_metrics")
    by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in _history().documents["cases"]:
        by_month[case["decided_on"].strftime("%Y-%m")].append(case)

    def rate(numerator: int, denominator: int) -> str:
        quotient = Decimal(numerator) / Decimal(denominator)
        return str(quotient.quantize(Decimal("0.0001"), ROUND_HALF_UP))

    expected = {}
    for month, cases in by_month.items():
        n = len(cases)
        days = sum((c["decided_on"].date() - c["received_on"].date()).days for c in cases)
        late = sum(c["decided_on"] > c["decision_deadline"] for c in cases)
        procedural = sum(c["denial_reason"] == "missing_information" for c in cases)
        expected[("average_days_to_decision", month)] = (rate(days, n), days, n)
        expected[("share_decided_within_deadline", month)] = (rate(n - late, n), n - late, n)
        expected[("procedural_denial_rate", month)] = (rate(procedural, n), procedural, n)

    assert {(i["metric"], i["period"]) for i in items} == set(expected)
    for item in items:
        assert (item["expected_value"], item["numerator"], item["denominator"]) == expected[
            (item["metric"], item["period"])
        ]
    assert len({i["period"] for i in items}) >= 6  # months with decisions: a real series
    assert Counter(i["metric"] for i in items).most_common(1)[0][1] == len(
        {i["period"] for i in items}
    )


def test_FR_003_10_manifest_hashes_identical_across_two_runs_with_same_seed(
    tmp_path: Path,
) -> None:
    first = generate_files(
        build_dataset(ANCHOR), build_history(build_dataset(ANCHOR)), tmp_path / "a"
    )
    second = generate_files(
        build_dataset(ANCHOR), build_history(build_dataset(ANCHOR)), tmp_path / "b"
    )

    assert first == second
    assert first["seed"] == SEED
    assert first["files"]
    assert "manifest.json" not in first["files"]
    for kind in ("testsets/known_answers.json", "history/explanations", "documents/"):
        assert any(name.startswith(kind) or name == kind for name in first["files"]), kind
    assert (tmp_path / "a" / "manifest.json").read_text() == (
        tmp_path / "b" / "manifest.json"
    ).read_text()
