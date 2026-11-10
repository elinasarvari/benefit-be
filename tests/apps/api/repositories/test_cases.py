"""Storage mapping of apps/api/repositories/cases.py (no database; T018)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from pymongo.errors import DuplicateKeyError

from apps.api.repositories.cases import _case_from_bson, _is_open_case_guard, bson_date, to_bson
from packages.domain.case import CaseState


def test_FR_001_23_calendar_dates_stored_as_midnight_utc_and_read_back() -> None:
    doc = to_bson({"received_on": date(2026, 3, 17), "state": CaseState.submitted})

    assert doc == {"received_on": datetime(2026, 3, 17, tzinfo=UTC), "state": "submitted"}
    assert bson_date(date(2026, 3, 17)) == doc["received_on"]


def test_FR_001_23_partial_index_violation_maps_to_open_case_guard() -> None:
    open_case = DuplicateKeyError("dup", 11000, {"keyPattern": {"household_id": 1}})
    same_case = DuplicateKeyError("dup", 11000, {"keyPattern": {"_id": 1}})

    assert _is_open_case_guard(open_case) is True
    assert _is_open_case_guard(same_case) is False


def test_FR_001_11_stored_case_round_trips_to_the_domain_model() -> None:
    case_id = "6f1c2a54-0b7e-4d3a-9c41-2e8f0d5b7a13"
    instant = datetime(2026, 3, 17, 15, tzinfo=UTC)
    stored = {
        "_id": case_id,
        "case_id": case_id,
        "household_id": case_id,
        "case_type": "application",
        "submission_reference": "sub-A",
        "state": "submitted",
        "open": True,
        "received_on": datetime(2026, 3, 17, tzinfo=UTC),
        "decision_deadline": datetime(2026, 5, 1, tzinfo=UTC),
        "clock": {"real_start": instant, "case_start": instant, "scale": 1, "offset_seconds": 0},
        "created_at": instant,
        "updated_at": instant,
    }

    case = _case_from_bson(stored)

    assert (case.received_on, case.decision_deadline) == (date(2026, 3, 17), date(2026, 5, 1))
