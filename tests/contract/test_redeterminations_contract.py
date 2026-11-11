"""007a: household and notice operations are real; response operations stay stubs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.export_openapi import OUTPUT


def operations() -> dict[str, dict[str, Any]]:
    schema = json.loads(Path(OUTPUT).read_text())
    return {
        op["operationId"]: {**op, "method": method, "path": path}
        for path, methods in schema["paths"].items()
        for method, op in methods.items()
    }


def test_FR_007_10_openapi_declares_household_and_notice_operations() -> None:
    ops = operations()

    household = ops["getHousehold"]["responses"]
    start = ops["startRedetermination"]["responses"]
    notices = ops["listCaseNotices"]

    assert "501" not in household and {"200", "401", "403", "404"} <= household.keys()
    assert {"200", "201", "403", "409", "503"} <= start.keys() and "501" not in start
    assert "household_has_open_case" in start["409"]["description"]
    assert "no_active_coverage_period" in start["409"]["description"]
    assert notices["method"] == "get" and notices["path"] == "/api/v1/cases/{case_id}/notices"
    assert "501" not in notices["responses"] and "404" in notices["responses"]


@pytest.mark.parametrize("operation_id", ["listMyOpenRequests", "respondToRequest"])
def test_FR_007_16_applicant_response_operations_stay_501_stubs(operation_id: str) -> None:
    op = operations()[operation_id]

    assert op["path"].startswith("/api/v1/me/requests")
    assert "501" in op["responses"]
