"""POST /api/v1/applications (001, T020, T063; TC-58, TC-102, TC-103)."""

from __future__ import annotations

import uuid

import pytest

from packages.domain.auth import Role
from packages.platform.settings import Settings
from tests.apps.api.case_fakes import CaseApiHarness
from tests.apps.api.forms import body_dict, form_dict

URL = "/api/v1/applications"


@pytest.fixture
def harness(settings: Settings) -> CaseApiHarness:
    return CaseApiHarness(settings)


def test_FR_001_01_submit_application_rejects_non_applicant_roles(harness: CaseApiHarness) -> None:
    body = body_dict(uuid.uuid4())

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
    harness.client.post(URL, json=body_dict(uuid.uuid4()))

    refused = harness.client.post(URL, json=body_dict(uuid.uuid4()))

    assert refused.status_code == 409
    error = refused.json()["error"]
    assert error["code"] == "conflict"
    assert error["fields"][0]["type"] == "household_has_open_case"


def test_FR_001_01_temporal_down_answers_503(harness: CaseApiHarness) -> None:
    harness.sign_in(Role.applicant)
    harness.workflows.fail_next_start = True

    response = harness.client.post(URL, json=body_dict(uuid.uuid4()))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "dependency_unavailable"


def test_FR_001_24_incomplete_form_returns_422_naming_each_dotted_field(
    harness: CaseApiHarness,
) -> None:
    harness.sign_in(Role.applicant)
    body = body_dict(uuid.uuid4())
    del body["form"]["members"][1]["date_of_birth"]
    del body["form"]["address"]["postal_code"]

    response = harness.client.post(URL, json=body)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    fields = {f["field"] for f in error["fields"]}
    assert {"body.form.members.1.date_of_birth", "body.form.address.postal_code"} <= fields
    assert "SYNTH-NAME" not in response.text  # submitted values are never echoed
    assert harness.repo.cases == {}
    assert harness.workflows.start_calls == 0


def test_FR_001_24_form_rules_are_enforced_by_the_api(harness: CaseApiHarness) -> None:
    harness.sign_in(Role.applicant)
    no_self = form_dict()
    no_self["members"][0]["relationship"] = "other"
    both = form_dict()
    both["members"][1]["jobs"] = both["members"][0]["jobs"]  # zero_income and jobs
    bad_zip = form_dict()
    bad_zip["address"]["postal_code"] = "6270"
    other_state = form_dict()
    other_state["address"]["state"] = "IN"

    for bad in (no_self, both, bad_zip, other_state):
        response = harness.client.post(
            URL, json={"submission_reference": str(uuid.uuid4()), "form": bad}
        )
        assert response.status_code == 422, bad

    assert harness.repo.cases == {}


def test_FR_001_25_refusal_409_body_has_conflict_reason_and_related_case_id(
    harness: CaseApiHarness,
) -> None:
    harness.sign_in(Role.applicant)
    first = harness.client.post(URL, json=body_dict(uuid.uuid4()))
    open_case_id = first.json()["case_id"]

    open_refusal = harness.client.post(URL, json=body_dict(uuid.uuid4()))

    assert open_refusal.status_code == 409
    error = open_refusal.json()["error"]
    assert error["code"] == "conflict"
    assert error["fields"][0]["type"] == "household_has_open_case"
    assert error["related_case_id"] == open_case_id


def test_FR_001_25_other_errors_have_null_related_case_id(harness: CaseApiHarness) -> None:
    harness.sign_in(Role.applicant)

    bad = harness.client.post(URL, json={"submission_reference": "nope"})

    assert bad.status_code == 422
    assert bad.json()["error"]["related_case_id"] is None
