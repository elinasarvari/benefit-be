"""MVP contract stubs (specs/mvp-contracts.md section 3): each endpoint exists with its roles and
answers 501 `not_implemented` until its feature lands. A feature deletes its rows as it builds."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from packages.domain.auth import Role
from packages.domain.errors import ErrorCode
from packages.platform.settings import Settings
from tests.apps.api.case_fakes import CaseApiHarness

ID = str(uuid.uuid4())
PAY_STUB = {
    "employee_name": "Synthetic Person",
    "employer": "Synthetic Employer",
    "frequency": "weekly",
    "gross_pay": "300.00",
    "pay_date": "2026-03-01",
    "content_type": "application/pdf",
    "size_bytes": 1024,
}

A, C, S = Role.applicant, Role.caseworker, Role.supervisor
APPLICANT_ONLY = (C, S)
STAFF_ONLY = (A,)

# (method, path, allowed role, every refused role, JSON body or raw bytes)
STUBS: list[tuple[str, str, Role, tuple[Role, ...], Any]] = [
    ("POST", f"/api/v1/cases/{ID}/documents", A, APPLICANT_ONLY, PAY_STUB),
    ("PUT", f"/api/v1/cases/{ID}/documents/{ID}/content", A, APPLICANT_ONLY, b"%PDF"),
    ("GET", f"/api/v1/cases/{ID}/verification", C, STAFF_ONLY, None),
    ("GET", f"/api/v1/cases/{ID}/explanations", C, STAFF_ONLY, None),
    ("GET", "/api/v1/me/history", A, APPLICANT_ONLY, None),
    ("GET", "/api/v1/me/notices", A, APPLICANT_ONLY, None),
    ("GET", f"/api/v1/me/notices/{ID}", A, APPLICANT_ONLY, None),
    ("GET", "/api/v1/me/requests", A, APPLICANT_ONLY, None),
    ("POST", f"/api/v1/me/requests/{ID}/response", A, APPLICANT_ONLY, {}),
    ("POST", f"/api/v1/cases/{ID}/advance-clock", S, (A, C), None),
    ("GET", "/api/v1/metrics?month=2026-03", C, STAFF_ONLY, None),
]


def _send(harness: CaseApiHarness, method: str, path: str, body: Any) -> Any:
    if isinstance(body, bytes):
        return harness.client.request(
            method, path, content=body, headers={"Content-Type": "application/pdf"}
        )
    return harness.client.request(method, path, json=body)


@pytest.mark.parametrize(("method", "path", "allowed", "refused", "body"), STUBS)
def test_mvp_contract_stub_answers_not_implemented_for_its_role(
    settings: Settings,
    method: str,
    path: str,
    allowed: Role,
    refused: tuple[Role, ...],
    body: Any,
) -> None:
    harness = CaseApiHarness(settings)
    harness.sign_in(allowed)
    response = _send(harness, method, path, body)
    assert response.status_code == 501, response.text
    assert response.json()["error"]["code"] == ErrorCode.not_implemented.value

    for role in refused:
        harness.sign_in(role)
        assert _send(harness, method, path, body).status_code == 403, role


def test_mvp_metrics_month_must_be_year_and_month(settings: Settings) -> None:
    harness = CaseApiHarness(settings)
    harness.sign_in(Role.supervisor)
    for month in ("March", "2026-13", "2026-00"):
        assert harness.client.get(f"/api/v1/metrics?month={month}").status_code == 422, month
