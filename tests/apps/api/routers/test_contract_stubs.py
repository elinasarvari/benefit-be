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

# (method, path, allowed role, refused role, JSON body or raw bytes)
STUBS: list[tuple[str, str, Role, Role, Any]] = [
    ("POST", f"/api/v1/cases/{ID}/documents", Role.applicant, Role.caseworker, PAY_STUB),
    ("PUT", f"/api/v1/cases/{ID}/documents/{ID}/content", Role.applicant, Role.caseworker, b"%PDF"),
    ("GET", f"/api/v1/cases/{ID}/verification", Role.caseworker, Role.applicant, None),
    ("GET", f"/api/v1/cases/{ID}/explanations", Role.caseworker, Role.applicant, None),
    ("GET", "/api/v1/me/history", Role.applicant, Role.caseworker, None),
    ("GET", "/api/v1/me/notices", Role.applicant, Role.caseworker, None),
    ("GET", f"/api/v1/me/notices/{ID}", Role.applicant, Role.caseworker, None),
    ("GET", "/api/v1/me/requests", Role.applicant, Role.caseworker, None),
    ("POST", f"/api/v1/me/requests/{ID}/response", Role.applicant, Role.caseworker, {}),
    ("POST", f"/api/v1/cases/{ID}/advance-clock", Role.supervisor, Role.caseworker, None),
    ("GET", f"/api/v1/households/{ID}", Role.caseworker, Role.applicant, None),
    (
        "POST",
        f"/api/v1/households/{ID}/redeterminations",
        Role.supervisor,
        Role.applicant,
        {"coverage_period_id": ID},
    ),
    ("GET", "/api/v1/metrics?month=2026-03", Role.caseworker, Role.applicant, None),
]


def _send(harness: CaseApiHarness, method: str, path: str, body: Any) -> Any:
    if isinstance(body, bytes):
        return harness.client.request(
            method, path, content=body, headers={"Content-Type": "application/pdf"}
        )
    return harness.client.request(method, path, json=body)


@pytest.mark.parametrize(("method", "path", "allowed", "refused", "body"), STUBS)
def test_mvp_contract_stub_answers_not_implemented_for_its_role(
    settings: Settings, method: str, path: str, allowed: Role, refused: Role, body: Any
) -> None:
    harness = CaseApiHarness(settings)
    harness.sign_in(allowed)
    response = _send(harness, method, path, body)
    assert response.status_code == 501, response.text
    assert response.json()["error"]["code"] == ErrorCode.not_implemented.value

    harness.sign_in(refused)
    assert _send(harness, method, path, body).status_code == 403


def test_mvp_metrics_month_must_be_year_and_month(settings: Settings) -> None:
    harness = CaseApiHarness(settings)
    harness.sign_in(Role.supervisor)
    assert harness.client.get("/api/v1/metrics?month=March").status_code == 422
