"""POST /api/v1/applications (001, T020; TC-58)."""

from __future__ import annotations

import uuid

import pytest

from packages.domain.auth import Role
from packages.platform.settings import Settings
from tests.apps.api.case_fakes import CaseApiHarness

URL = "/api/v1/applications"


@pytest.fixture
def harness(settings: Settings) -> CaseApiHarness:
    return CaseApiHarness(settings)


def test_FR_001_01_submit_application_rejects_non_applicant_roles(harness: CaseApiHarness) -> None:
    body = {"submission_reference": str(uuid.uuid4())}

    for role in (Role.caseworker, Role.supervisor):
        harness.sign_in(role)
        response = harness.client.post(URL, json=body)
        assert response.status_code == 403, role
        assert response.json()["error"]["code"] == "auth.forbidden"
    harness.sign_out()
    anonymous = harness.client.post(URL, json=body)

    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "auth.required"
    assert harness.repo.cases == {}
    assert harness.workflows.start_calls == 0

    harness.sign_in(Role.applicant)
    created = harness.client.post(URL, json=body)
    again = harness.client.post(URL, json=body)
    assert (created.status_code, again.status_code) == (201, 200)
    assert created.json()["state"] == "submitted"


def test_FR_001_23_refusal_reason_is_in_error_fields_type(harness: CaseApiHarness) -> None:
    harness.sign_in(Role.applicant)
    harness.client.post(URL, json={"submission_reference": str(uuid.uuid4())})

    refused = harness.client.post(URL, json={"submission_reference": str(uuid.uuid4())})

    assert refused.status_code == 409
    error = refused.json()["error"]
    assert error["code"] == "conflict"
    assert error["fields"][0]["type"] == "household_has_open_case"


def test_FR_001_01_temporal_down_answers_503(harness: CaseApiHarness) -> None:
    harness.sign_in(Role.applicant)
    harness.workflows.fail_next_start = True

    response = harness.client.post(URL, json={"submission_reference": str(uuid.uuid4())})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "dependency_unavailable"
