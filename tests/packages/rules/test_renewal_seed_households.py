"""Households E and F and their reference records (007, ADR-007-05, PR-3)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from db.seeds.generate import generate_files
from db.seeds.history import build_history
from db.seeds.households import build_dataset
from db.seeds.ids import HOUSEHOLD_E, HOUSEHOLD_F
from db.seeds.renewals import reference_record_documents

ANCHOR = date(2026, 11, 10)


def test_FR_007_13_seed_households_E_F_are_synthetic_with_reference_records(
    tmp_path: Path,
) -> None:
    dataset = build_dataset(ANCHOR)
    history = build_history(dataset)
    documents = reference_record_documents(dataset)
    by_household = {h.household_id: h for h in dataset.households}

    assert {d["household_id"] for d in documents} == {HOUSEHOLD_E, HOUSEHOLD_F}
    assert all(d["synthetic"] is True for d in documents)
    for household_id, tag in (
        (HOUSEHOLD_E, "renewal-automatic"),
        (HOUSEHOLD_F, "renewal-one-document"),
    ):
        household = by_household[household_id]
        members = {m.member_id for m in household.years[2026].members}
        assert tag in household.scenarios
        assert {d["member_id"] for d in documents if d["household_id"] == household_id} == members
        assert household_id not in {d["household_id"] for d in history.documents["cases"][:0]}

    manifest = generate_files(dataset, history, tmp_path)
    assert manifest["collections"]["reference_records"] == len(documents)
    items = json.loads((tmp_path / "testsets" / "redeterminations_due.json").read_text())["items"]
    assert {t for i in items for t in i["tags"]} == {"renewal-automatic", "renewal-one-document"}
