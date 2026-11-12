"""POST /cases/{id}/documents and PUT .../content (TC-37 to TC-42, TC-45, TC-48; FR-002-17, -21)."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import pytest

from packages.domain.auth import Role
from packages.domain.case import case_id_for
from packages.platform.settings import Settings
from tests.apps.api.document_fakes import (
    JPEG,
    PDF,
    PNG,
    STUB_BODY,
    DocumentsHarness,
)
from tests.apps.api.forms import body_dict
from tests.apps.api.routers.test_cases import put_case

MB = 1024 * 1024


@pytest.fixture
def harness(settings: Settings) -> DocumentsHarness:
    h = DocumentsHarness(settings)
    h.sign_in(Role.applicant)
    return h


def _uploaded(harness: DocumentsHarness, case_id: uuid.UUID) -> str:
    created = harness.create_stub(case_id)
    assert created.status_code == 201, created.text
    return str(created.json()["document_id"])


def test_FR_002_17_applicant_creates_and_uploads_stub(harness: DocumentsHarness) -> None:
    case = put_case(harness)

    created = harness.create_stub(case.case_id)
    assert created.status_code == 201
    document_id = uuid.UUID(created.json()["document_id"])

    stored = harness.put_content(case.case_id, document_id, PDF)

    assert stored.status_code == 204
    assert stored.content == b""
    document = harness.documents.documents[document_id]
    assert document.household_id == case.household_id  # from the session
    assert document.sha256 == hashlib.sha256(PDF).hexdigest()
    assert document.content_uploaded_at is not None
    assert harness.blobs.get(f"{case.household_id}/{document_id}") == PDF
    details = harness.documents.details[document_id]
    assert details.employer == STUB_BODY["employer"]
    assert str(details.gross_pay) == "300.00"


def test_FR_002_17_upload_before_submit_case_not_yet_created(harness: DocumentsHarness) -> None:
    reference = uuid.uuid4()
    case_id = case_id_for(reference)
    assert case_id not in harness.repo.cases

    document_id = _uploaded(harness, case_id)
    assert harness.put_content(case_id, document_id, PDF).status_code == 204

    submitted = harness.client.post("/api/v1/applications", json=body_dict(reference))
    assert submitted.status_code in (200, 201), submitted.text
    assert case_id in harness.repo.cases

    harness.sign_in(Role.caseworker)
    verification = harness.client.get(f"/api/v1/cases/{case_id}/verification")
    assert verification.status_code == 200
    assert [d["document_id"] for d in verification.json()["documents"]] == [document_id]


def test_FR_002_17_eleventh_document_refused_422(harness: DocumentsHarness) -> None:
    case = put_case(harness)
    for _ in range(10):
        _uploaded(harness, case.case_id)

    refused = harness.create_stub(case.case_id)

    assert refused.status_code == 422
    error = refused.json()["error"]
    assert error["code"] == "validation_failed"
    assert "10" in error["message"]
    assert len(harness.documents.documents) == 10


def test_FR_002_17_file_over_10mb_refused_413(harness: DocumentsHarness) -> None:
    case = put_case(harness)
    document_id = _uploaded(harness, case.case_id)

    refused = harness.put_content(case.case_id, document_id, PDF + b"0" * (10 * MB))

    assert refused.status_code == 413
    message = refused.json()["error"]["message"]
    assert "10 MB" in message and "PDF" in message and "PNG" in message and "JPEG" in message
    assert harness.blobs.blobs == {}


def test_FR_002_17_file_of_exactly_10mb_is_accepted(harness: DocumentsHarness) -> None:
    case = put_case(harness)
    document_id = _uploaded(harness, case.case_id)

    stored = harness.put_content(case.case_id, document_id, PDF + b"0" * (10 * MB - len(PDF)))

    assert stored.status_code == 204


def test_FR_002_17_wrong_type_or_magic_bytes_refused_415(harness: DocumentsHarness) -> None:
    case = put_case(harness)
    document_id = _uploaded(harness, case.case_id)
    attempts: list[tuple[bytes, str]] = [
        (b"MZ\x90\x00 executable", "application/x-msdownload"),  # .exe
        (PNG, "application/pdf"),  # a PNG labelled as PDF
        (b"plain text, not a pdf", "application/pdf"),
        (PDF, "text/plain"),
        (PDF, ""),
    ]

    for data, content_type in attempts:
        refused = harness.put_content(case.case_id, document_id, data, content_type)
        assert refused.status_code == 415, (content_type, refused.text)
        message = refused.json()["error"]["message"]
        assert "10 MB" in message and "PDF" in message and "PNG" in message and "JPEG" in message

    assert harness.blobs.blobs == {}


def test_FR_002_17_content_type_must_match_the_recorded_stub(harness: DocumentsHarness) -> None:
    case = put_case(harness)
    document_id = _uploaded(harness, case.case_id)  # recorded as application/pdf

    assert harness.put_content(case.case_id, document_id, JPEG, "image/jpeg").status_code == 415


def test_FR_002_17_png_and_jpeg_are_accepted(harness: DocumentsHarness) -> None:
    case = put_case(harness)
    for data, media in ((PNG, "image/png"), (JPEG, "image/jpeg")):
        created = harness.create_stub(case.case_id, content_type=media, size_bytes=len(data))
        document_id = created.json()["document_id"]
        assert harness.put_content(case.case_id, document_id, data, media).status_code == 204


def test_FR_002_17_put_same_bytes_is_idempotent(harness: DocumentsHarness) -> None:
    case = put_case(harness)
    document_id = _uploaded(harness, case.case_id)

    first = harness.put_content(case.case_id, document_id, PDF)
    second = harness.put_content(case.case_id, document_id, PDF)

    assert (first.status_code, second.status_code) == (204, 204)
    assert len(harness.blobs.blobs) == 1
    assert len(harness.documents.documents) == 1


def test_FR_002_17_content_for_unknown_document_404(harness: DocumentsHarness) -> None:
    case = put_case(harness)
    other = put_case_document(harness)

    for document_id in (uuid.uuid4(), other):  # unknown, and a document of another case
        refused = harness.put_content(case.case_id, document_id, PDF)
        assert refused.status_code == 404
        assert refused.json()["error"]["code"] == "not_found"


def put_case_document(harness: DocumentsHarness) -> uuid.UUID:
    other_case = case_id_for(uuid.uuid4())
    return uuid.UUID(_uploaded(harness, other_case))


@pytest.mark.parametrize(
    "missing", ["employee_name", "employer", "frequency", "gross_pay", "pay_date"]
)
def test_FR_002_21_stub_missing_a_detail_cannot_be_created(
    harness: DocumentsHarness, missing: str
) -> None:
    case = put_case(harness)
    body: dict[str, Any] = {k: v for k, v in STUB_BODY.items() if k != missing}

    refused = harness.client.post(f"/api/v1/cases/{case.case_id}/documents", json=body)

    assert refused.status_code == 422
    fields = [f["field"] for f in refused.json()["error"]["fields"]]
    assert any(name.endswith(missing) for name in fields), fields
    assert harness.documents.documents == {}


@pytest.mark.parametrize("blank", ["employee_name", "employer"])
def test_FR_002_21_blank_text_detail_cannot_be_created(
    harness: DocumentsHarness, blank: str
) -> None:
    case = put_case(harness)

    refused = harness.create_stub(case.case_id, **{blank: ""})

    assert refused.status_code == 422
    assert harness.documents.documents == {}
