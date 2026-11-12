"""GET /cases/{id}/verification (TC-49 to TC-52; FR-002-18, PL-7)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from apps.api.repositories.documents import StoredDocument
from packages.domain.auth import Role
from packages.domain.verification import (
    Finding,
    FindingCause,
    PayStubDetails,
    VerificationResult,
)
from packages.platform.settings import Settings
from tests.apps.api.document_fakes import OTHER_HOUSEHOLD, STUB_BODY, DocumentsHarness
from tests.apps.api.fakes import HOUSEHOLD
from tests.apps.api.routers.test_cases import put_case


@pytest.fixture
def harness(settings: Settings) -> DocumentsHarness:
    return DocumentsHarness(settings)


def url(case_id: object) -> str:
    return f"/api/v1/cases/{case_id}/verification"


def add_stub(harness: DocumentsHarness, case_id: uuid.UUID, household: uuid.UUID) -> uuid.UUID:
    document_id = uuid.uuid4()
    now = harness.clock.now()
    harness.documents.documents[document_id] = StoredDocument(
        document_id=document_id,
        case_id=case_id,
        household_id=household,
        content_type="application/pdf",
        size_bytes=100,
        created_at=now,
        content_uploaded_at=now,
    )
    harness.documents.details[document_id] = PayStubDetails(
        document_id=document_id,
        case_id=case_id,
        household_id=household,
        employee_name=str(STUB_BODY["employee_name"]),
        employer=str(STUB_BODY["employer"]),
        frequency="weekly",
        gross_pay=Decimal("400.00"),
        pay_date="2026-03-01",
    )
    return document_id


def test_FR_002_18_get_verification_returns_documents_details_findings(
    harness: DocumentsHarness,
) -> None:
    case = put_case(harness)
    document_id = add_stub(harness, case.case_id, case.household_id)
    finding = Finding(
        finding_id=uuid.uuid4(),
        cause=FindingCause.income_mismatch,
        member_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        document_id=document_id,
        declared_monthly=Decimal("1300.00"),
        documented_monthly=Decimal("1733.33"),
        conversion="weekly 400.00 x 52 / 12 = 1733.33",
    )
    harness.documents.results[case.case_id] = VerificationResult(
        complete=True, needs_review=True, case_id=case.case_id, findings=[finding]
    )
    harness.sign_in(Role.caseworker)

    response = harness.client.get(url(case.case_id))

    assert response.status_code == 200
    body = response.json()
    [document] = body["documents"]
    assert document["document_id"] == str(document_id)
    assert document["content_type"] == "application/pdf"
    assert document["details"]["employer"] == STUB_BODY["employer"]
    assert document["details"]["gross_pay"] == "400.00"
    [found] = body["result"]["findings"]
    assert found["cause"] == "income_mismatch"
    assert (found["declared_monthly"], found["documented_monthly"]) == ("1300.00", "1733.33")
    assert found["conversion"] == "weekly 400.00 x 52 / 12 = 1733.33"
    assert found["document_id"] == str(document_id)


def test_FR_002_18_supervisor_may_read_verification(harness: DocumentsHarness) -> None:
    case = put_case(harness)
    harness.sign_in(Role.supervisor)

    assert harness.client.get(url(case.case_id)).status_code == 200


def test_FR_002_18_verification_result_null_before_first_check(harness: DocumentsHarness) -> None:
    case = put_case(harness)
    harness.sign_in(Role.caseworker)

    response = harness.client.get(url(case.case_id))

    assert response.status_code == 200
    assert response.json() == {"documents": [], "result": None}


def test_FR_002_18_verification_refused_for_non_staff_roles(harness: DocumentsHarness) -> None:
    other = put_case(harness, household=OTHER_HOUSEHOLD)
    add_stub(harness, other.case_id, OTHER_HOUSEHOLD)
    own = put_case(harness, household=HOUSEHOLD)

    harness.sign_in(Role.applicant)
    for case in (own, other):  # own household's case, and another household's: same refusal
        refused = harness.client.get(url(case.case_id))
        assert refused.status_code == 403
        assert str(STUB_BODY["employer"]) not in refused.text

    harness.sign_out()
    assert harness.client.get(url(own.case_id)).status_code == 401
    assert harness.documents.calls == []  # no refused caller reached the repository


def test_FR_002_18_verification_unknown_case_404(harness: DocumentsHarness) -> None:
    harness.sign_in(Role.caseworker)

    response = harness.client.get(url(uuid.uuid4()))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
