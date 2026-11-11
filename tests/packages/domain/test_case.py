from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from apps.api.schemas.cases import ReviewActionBody
from packages.domain.case import (
    SUBMISSION_NS,
    ActionType,
    ApplicantMember,
    ApplicationForm,
    ApplicationSubmission,
    CaseState,
    CaseStatus,
    IllinoisAddress,
    Job,
    StoredApplication,
    case_id_for,
    workflow_id_for,
)


def test_FR_001_01_case_id_for_same_reference_is_stable() -> None:
    first = case_id_for("sub-A")
    assert isinstance(first, UUID)
    assert case_id_for("sub-A") == first
    assert case_id_for("sub-B") != first
    assert first.version == 5
    assert SUBMISSION_NS != first
    assert workflow_id_for(first) == f"case-{first}"


def _status(days_remaining: int | None) -> CaseStatus:
    return CaseStatus(
        case_id=uuid4(),
        state=CaseState.decided if days_remaining is None else CaseState.verifying,
        received_on=date(2026, 3, 1),
        decision_deadline=date(2026, 4, 15),
        days_remaining=days_remaining,
    )


@pytest.mark.parametrize(
    ("days_remaining", "expected"),
    [(11, False), (10, True), (9, True), (0, True), (None, False)],
)
def test_FR_001_11_case_status_at_risk_at_ten_days_or_fewer(
    days_remaining: int | None, expected: bool
) -> None:
    assert _status(days_remaining).at_risk is expected


def _override(reason: str | None) -> ReviewActionBody:
    """The REST body: the signal itself is 010's `CaseworkerActionSignal`, whose reason the
    workflow checks (ADR-001-11, FR-010-05); the API refuses an override without one first."""
    return ReviewActionBody(
        action=ActionType.override_to_approve, reason=reason, request_id=uuid4()
    )


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_FR_001_20_override_request_requires_reason_up_to_500_chars(reason: str | None) -> None:
    with pytest.raises(ValidationError):
        _override(reason)


def test_FR_001_20_override_request_accepts_500_chars_rejects_501() -> None:
    assert _override("x" * 500).reason == "x" * 500
    with pytest.raises(ValidationError):
        _override("x" * 501)


def test_FR_001_20_approve_denial_needs_no_reason() -> None:
    request = ReviewActionBody(action=ActionType.approve_denial, request_id=uuid4())
    assert request.reason is None


# --- Application form (ADR-001-08, FR-001-24) ---------------------------------------------------


def _job(**over: object) -> dict:
    base = dict(employer="Acme Test Co", pay_frequency="monthly", declared_monthly_gross="1250.00")
    return base | over


def _member(relationship: str = "self", **over: object) -> dict:
    base = dict(
        full_name="Test Person",
        date_of_birth="1980-01-01",
        relationship=relationship,
        citizenship_status="us_citizen",
        medicare_entitled=False,
        zero_income=True,
        jobs=[],
    )
    return base | over


def _address(**over: object) -> dict:
    base = dict(line1="1 Synthetic St", city="Chicago", state="IL", postal_code="60601")
    return base | over


def _form(members: list[dict] | None = None) -> dict:
    return dict(members=members if members is not None else [_member()], address=_address())


def _members(n: int) -> list[dict]:
    return [_member()] + [_member("child") for _ in range(n - 1)]


@pytest.mark.parametrize(
    ("members", "ok"),
    [
        ([], False),
        (_members(1), True),
        (_members(10), True),
        (_members(11), False),
        ([_member(), _member()], False),
        ([_member("spouse")], False),
    ],
)
def test_FR_001_24_form_requires_one_to_ten_members_exactly_one_self(
    members: list[dict], ok: bool
) -> None:
    if ok:
        ApplicationForm.model_validate(_form(members))
    else:
        with pytest.raises(ValidationError):
            ApplicationForm.model_validate(_form(members))


@pytest.mark.parametrize(
    ("over", "ok"),
    [
        (dict(zero_income=True, jobs=[_job()]), False),
        (dict(zero_income=False, jobs=[]), False),
        (dict(zero_income=False, jobs=[_job()] * 5), True),
        (dict(zero_income=False, jobs=[_job()] * 6), False),
    ],
)
def test_FR_001_24_member_has_zero_income_xor_jobs(over: dict, ok: bool) -> None:
    if ok:
        ApplicantMember.model_validate(_member(**over))
    else:
        with pytest.raises(ValidationError):
            ApplicantMember.model_validate(_member(**over))


@pytest.mark.parametrize(
    ("over", "ok"),
    [
        (dict(state="WI"), False),
        (dict(postal_code="6060"), False),
        (dict(postal_code="60601-1234"), False),
        (dict(postal_code="60601"), True),
        (dict(line2="x" * 101), False),
        (dict(line2="x" * 100), True),
    ],
)
def test_FR_001_24_address_must_be_illinois_with_five_digit_zip(over: dict, ok: bool) -> None:
    if ok:
        IllinoisAddress.model_validate(_address(**over))
    else:
        with pytest.raises(ValidationError):
            IllinoisAddress.model_validate(_address(**over))


def test_FR_001_24_member_and_job_field_bounds() -> None:
    today = date.today()
    for m in (
        _member(full_name=""),
        _member(full_name="x" * 101),
        _member(date_of_birth=today.isoformat()),
        _member(date_of_birth=(today + timedelta(days=1)).isoformat()),
    ):
        with pytest.raises(ValidationError):
            ApplicantMember.model_validate(m)
    for gross in ("-0.01", "1.005"):
        with pytest.raises(ValidationError):
            Job.model_validate(_job(declared_monthly_gross=gross))
    job = Job.model_validate(_job())
    assert job.declared_monthly_gross == Decimal("1250.00")
    assert job.model_dump(mode="json")["declared_monthly_gross"] == "1250.00"


def test_FR_001_24_submission_forbids_extra_fields_including_household_id() -> None:
    body = dict(submission_reference=str(uuid4()), form=_form())
    ApplicationSubmission.model_validate(body)
    with pytest.raises(ValidationError):
        ApplicationSubmission.model_validate(body | dict(household_id=str(uuid4())))
    with pytest.raises(ValidationError):
        ApplicationSubmission.model_validate(
            dict(
                submission_reference=str(uuid4()),
                form=_form([_member() | dict(ssn="000-00-0000")]),
            )
        )


def test_FR_001_24_stored_application_requires_synthetic_true_and_id_equals_case_id() -> None:
    case_id = uuid4()
    doc = dict(
        _id=case_id,
        case_id=case_id,
        household_id=uuid4(),
        submission_reference=str(uuid4()),
        form=_form(),
        submitted_at="2026-01-01T00:00:00Z",
        synthetic=True,
    )
    StoredApplication.model_validate(doc)
    missing = {k: v for k, v in doc.items() if k != "synthetic"}
    for bad in (doc | dict(synthetic=False), missing, doc | dict(_id=uuid4())):
        with pytest.raises(ValidationError):
            StoredApplication.model_validate(bad)
