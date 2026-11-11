"""apps/api/repositories/explanations.py: stored document to domain model (FR-004-13)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from apps.api.repositories.explanations import explanation_from_bson


def test_FR_004_13_stored_explanation_round_trips_with_bson_dates() -> None:
    stored = {
        "_id": uuid.uuid4(),
        "determination_id": uuid.uuid4(),
        "case_id": uuid.uuid4(),
        "household_id": uuid.uuid4(),
        "audience": "applicant",
        "result": "ineligible",
        "text": "Your household income is over the limit.",
        "citations": [
            {
                "rule_id": "ELIG-06",
                "section": "TM-3.1",
                "title": "Income limit",
                "manual_version": datetime(2026, 1, 1, tzinfo=UTC),
            }
        ],
        "built_at": datetime(2026, 3, 17, 15, 0),
        "synthetic": True,
    }

    explanation = explanation_from_bson(stored)

    assert explanation.citations[0].manual_version == date(2026, 1, 1)
    assert explanation.built_at == datetime(2026, 3, 17, 15, 0, tzinfo=UTC)
    assert explanation.text == stored["text"]
