"""Eligibility models match the committed JSON schema (FR-003-05, FR-003-19).

A change here is a contract change: regenerate `snapshots/eligibility_models.json` in its own PR
with `uv run python -m tests.contract.test_eligibility_models`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from packages.domain.eligibility import Determination, DeterminationRecord, DeterminationView
from packages.domain.household import HouseholdSnapshot, VerifiedMemberFacts

SNAPSHOT = Path(__file__).parent / "snapshots" / "eligibility_models.json"


def live_schemas() -> dict[str, Any]:
    return {
        model.__name__: model.model_json_schema()
        for model in (
            HouseholdSnapshot,
            VerifiedMemberFacts,
            Determination,
            DeterminationRecord,
            DeterminationView,
        )
    }


def test_FR_003_05_eligibility_models_match_committed_schema() -> None:
    saved = json.loads(SNAPSHOT.read_text())

    assert json.loads(json.dumps(live_schemas())) == saved


if __name__ == "__main__":
    SNAPSHOT.parent.mkdir(exist_ok=True)
    SNAPSHOT.write_text(json.dumps(live_schemas(), indent=2, sort_keys=True) + "\n")
