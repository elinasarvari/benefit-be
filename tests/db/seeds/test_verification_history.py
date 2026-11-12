"""Seeded pay stub checks of history cases (002, db/seeds/verification_history.py; TC-69, TC-70)."""

from __future__ import annotations

from collections import Counter
from datetime import date

import pytest

from db.seeds.history import build_history
from db.seeds.households import build_dataset
from db.seeds.ids import SEED_ANCHOR_DATE
from db.seeds.verification_history import COLLECTIONS

# The default anchor and others where a household's prior and current cases share a coverage year.
ANCHORS = [
    SEED_ANCHOR_DATE,
    date(2026, 6, 1),
    date(2026, 3, 1),
    date(2025, 12, 1),
    date(2026, 9, 15),
]


@pytest.mark.parametrize("anchor", ANCHORS, ids=str)
def test_FR_002_18_seeded_history_ids_are_unique_per_collection(anchor: date) -> None:
    documents = build_history(build_dataset(anchor)).documents
    for name in COLLECTIONS:
        counts = Counter(doc["_id"] for doc in documents[name])
        duplicates = [i for i, n in counts.items() if n > 1]
        assert duplicates == [], f"{name}: {len(duplicates)} duplicate _id at anchor {anchor}"


def test_FR_002_18_seeded_history_check_is_deterministic() -> None:
    first = build_history(build_dataset(SEED_ANCHOR_DATE)).documents
    second = build_history(build_dataset(SEED_ANCHOR_DATE)).documents
    for name in COLLECTIONS:
        assert first[name] == second[name], name


def test_FR_002_03_seeded_history_includes_income_mismatch_findings() -> None:
    results = build_history(build_dataset(SEED_ANCHOR_DATE)).documents["verification_results"]
    mismatched = [r for r in results if any(f["cause"] == "income_mismatch" for f in r["findings"])]
    assert mismatched, "TC-70 needs at least one seeded case with an income mismatch"
    for result in results:
        details_ids = {a["document_id"] for a in result["stub_assignments"]}
        assert all(isinstance(i, type(result["case_id"])) for i in details_ids)
