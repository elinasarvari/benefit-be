"""Expected renewal paths of seed households E and F (007, FR-007-02, FR-007-03)."""

from __future__ import annotations

import json
from datetime import date

import pytest

from db.seeds.generate import generate_files
from db.seeds.history import build_history
from db.seeds.households import build_dataset
from db.seeds.ids import HOUSEHOLD_E, HOUSEHOLD_F
from db.seeds.renewals import household_members, reference_answers
from packages.domain.renewal import RenewalItemKind
from packages.rules.renewal import renewal_items

ANCHOR = date(2026, 11, 10)


@pytest.fixture(scope="module")
def due(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, str]]:
    out = tmp_path_factory.mktemp("generated")
    dataset = build_dataset(ANCHOR)
    generate_files(dataset, build_history(dataset), out)
    items = json.loads((out / "testsets" / "redeterminations_due.json").read_text())["items"]
    return {i["household_id"]: i for i in items}


def test_FR_007_02_household_E_renews_automatically(due: dict[str, dict[str, str]]) -> None:
    item = due[str(HOUSEHOLD_E)]
    assert item["expected_path"] == "renew_automatically"
    assert 0 <= (date.fromisoformat(item["redetermination_start"]) - ANCHOR).days <= 30
    dataset = build_dataset(ANCHOR)
    household = next(h for h in dataset.households if h.household_id == HOUSEHOLD_E)
    assert (
        renewal_items(household_members(household), reference_answers(dataset)[HOUSEHOLD_E]) == []
    )


def test_FR_007_03_household_F_expected_path_is_one_document_request(
    due: dict[str, dict[str, str]],
) -> None:
    assert due[str(HOUSEHOLD_F)]["expected_path"] == "ask_applicant"
    dataset = build_dataset(ANCHOR)
    household = next(h for h in dataset.households if h.household_id == HOUSEHOLD_F)
    items = renewal_items(household_members(household), reference_answers(dataset)[HOUSEHOLD_F])
    assert [i.kind for i in items] == [RenewalItemKind.income_differs]
