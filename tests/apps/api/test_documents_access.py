"""Who may upload, and what the logs never carry (TC-43, TC-44, TC-57; PL-6, PL-7, PL-11)."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator, MutableMapping
from typing import Any

import pytest
import structlog.testing

from apps.api.repositories.documents import StoredDocument
from packages.domain.auth import Role
from packages.domain.case import ActionType, AllowedActions, case_id_for
from packages.domain.verification import VerificationResult
from packages.platform.settings import Settings
from tests.apps.api.document_fakes import (
    OTHER_HOUSEHOLD,
    PDF,
    STUB_BODY,
    DocumentsHarness,
)
from tests.apps.api.fakes import HOUSEHOLD
from tests.apps.api.routers.test_cases import put_case


@pytest.fixture
def harness(settings: Settings) -> DocumentsHarness:
    return DocumentsHarness(settings)


def test_FR_002_17_upload_refused_for_another_households_case(harness: DocumentsHarness) -> None:
    case = put_case(harness, household=OTHER_HOUSEHOLD)
    harness.sign_in(Role.applicant)  # household HOUSEHOLD, not the case's

    refused = harness.create_stub(case.case_id)

    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "auth.forbidden"
    assert harness.documents.documents == {}


def test_FR_002_17_upload_refused_for_another_households_pre_case_upload(
    harness: DocumentsHarness,
) -> None:
    """The case is not created yet, but an earlier upload against its ID belongs to household B."""
    case_id = case_id_for(uuid.uuid4())
    document_id = uuid.uuid4()
    harness.documents.documents[document_id] = StoredDocument(
        document_id=document_id,
        case_id=case_id,
        household_id=OTHER_HOUSEHOLD,
        content_type="application/pdf",
        size_bytes=10,
        created_at=harness.clock.now(),
    )
    harness.sign_in(Role.applicant)

    assert harness.create_stub(case_id).status_code == 403
    assert harness.put_content(case_id, document_id, PDF).status_code == 403
    assert harness.blobs.blobs == {}


def test_FR_002_17_household_comes_from_the_session_not_the_body(
    harness: DocumentsHarness,
) -> None:
    harness.sign_in(Role.applicant)
    case_id = case_id_for(uuid.uuid4())

    extra = harness.client.post(
        f"/api/v1/cases/{case_id}/documents",
        json={**STUB_BODY, "household_id": str(OTHER_HOUSEHOLD)},
    )

    assert extra.status_code == 422  # unknown field refused
    assert harness.documents.documents == {}


def test_FR_002_17_upload_routes_refused_for_every_non_applicant_role(
    harness: DocumentsHarness,
) -> None:
    case = put_case(harness, household=HOUSEHOLD)
    document_id = uuid.uuid4()
    post = f"/api/v1/cases/{case.case_id}/documents"
    put = f"/api/v1/cases/{case.case_id}/documents/{document_id}/content"

    for role in (Role.caseworker, Role.supervisor):
        harness.sign_in(role)
        assert harness.client.post(post, json=STUB_BODY).status_code == 403, role
        assert harness.client.put(put, content=PDF).status_code == 403, role
    # The system has no admin role (Role has three values), so these are every non-applicant.
    harness.sign_out()
    assert harness.client.post(post, json=STUB_BODY).status_code == 401
    assert harness.client.put(put, content=PDF).status_code == 401
    assert harness.documents.documents == {}
    assert harness.blobs.blobs == {}


@pytest.fixture
def captured() -> Iterator[list[MutableMapping[str, Any]]]:
    with structlog.testing.capture_logs() as logs:
        yield logs


def test_FR_002_17_no_names_reasons_or_bytes_in_logs(
    harness: DocumentsHarness,
    captured: list[MutableMapping[str, Any]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    secret_bytes = b"%PDF-1.4 SECRET-BYTES-MARKER"
    reason = "REASON-TEXT-MARKER"
    case = put_case(harness)
    harness.sign_in(Role.applicant)

    created = harness.create_stub(case.case_id, size_bytes=len(secret_bytes))
    document_id = created.json()["document_id"]
    harness.put_content(case.case_id, document_id, secret_bytes)
    harness.documents.results[case.case_id] = VerificationResult(
        complete=True, needs_review=False, case_id=case.case_id
    )
    harness.sign_in(Role.caseworker)
    harness.client.get(f"/api/v1/cases/{case.case_id}/verification")
    task = uuid.uuid4()
    harness.workflows.allowed = AllowedActions(task_id=task, actions=[ActionType.dismiss_finding])
    harness.client.post(
        f"/api/v1/cases/{case.case_id}/review-tasks/{task}/actions",
        json={
            "action": "dismiss_finding",
            "reason": reason,
            "request_id": str(uuid.uuid4()),
            "finding_id": str(uuid.uuid4()),
        },
    )

    text = " ".join([str(entry) for entry in captured] + [r.getMessage() for r in caplog.records])
    for marker in (
        str(STUB_BODY["employee_name"]),
        str(STUB_BODY["employer"]),
        "SECRET-BYTES-MARKER",
        reason,
    ):
        assert marker not in text, marker
