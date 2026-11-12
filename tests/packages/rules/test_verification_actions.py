"""Acceptance of a correction or dismissal (FR-002-19; ADR-002-06; KD-3).

Synthetic data only (PR-3).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from packages.domain.case import PayFrequency
from packages.domain.verification import (
    Finding,
    FindingCause,
    PayStubDetails,
    VerificationResult,
)
from packages.rules.verification_actions import (
    ActionRefused,
    accept_correction,
    accept_dismissal,
    parse_field_ref,
)

NS = uuid.uuid5(uuid.NAMESPACE_URL, "https://benefit-case-platform.example/actions")
DOC = uuid.uuid5(NS, "doc")
REVIEWER = uuid.uuid5(NS, "reviewer")
NOW = datetime(2026, 6, 1, tzinfo=UTC)


def details() -> PayStubDetails:
    return PayStubDetails(
        document_id=DOC,
        case_id=uuid.uuid5(NS, "case"),
        household_id=uuid.uuid5(NS, "household"),
        employee_name="Ana Rivera",
        employer="Acme Works",
        frequency=PayFrequency.monthly,
        gross_pay=Decimal("3000.00"),
        pay_date=date(2026, 5, 20),
    )


def correct(stored: list[PayStubDetails], action: uuid.UUID, ref: str, value: object):
    return accept_correction(
        stored,
        action_id=action,
        field_ref=ref,
        value=value,
        reviewer_id=REVIEWER,
        reason="typo",
        submitted_at=NOW,
    )


def result_with(finding_id: uuid.UUID) -> VerificationResult:
    finding = Finding(finding_id=finding_id, cause=FindingCause.income_mismatch)
    return VerificationResult(complete=False, needs_review=True, findings=[finding])


@settings(max_examples=50)
@given(st.text(max_size=40))
def test_FR_002_19_malformed_field_ref_always_refused(text: str) -> None:
    with pytest.raises(ActionRefused) as refused:
        parse_field_ref(text)
    assert refused.value.code == "unknown_field"


def test_FR_002_19_valid_field_ref_parses() -> None:
    assert parse_field_ref(f"pay_stub:{DOC}:gross_pay") == (DOC, "gross_pay")


def test_FR_002_19_unknown_field_name_refused() -> None:
    with pytest.raises(ActionRefused) as refused:
        parse_field_ref(f"pay_stub:{DOC}:member_id")
    assert refused.value.code == "unknown_field"


@settings(max_examples=50)
@given(st.uuids(), st.text(alphabet="abc-", max_size=10))
def test_FR_002_19_unknown_document_or_field_ref_refused(doc: uuid.UUID, junk: str) -> None:
    ref = f"pay_stub:{doc}:{junk}"
    with pytest.raises(ActionRefused) as refused:
        correct([details()], uuid.uuid4(), ref, "100.00")
    assert refused.value.code == "unknown_field"


def test_FR_002_19_unknown_document_refused() -> None:
    other = uuid.uuid5(NS, "other")
    with pytest.raises(ActionRefused) as refused:
        correct([details()], uuid.uuid4(), f"pay_stub:{other}:gross_pay", "100.00")
    assert refused.value.code == "unknown_field"


def test_FR_002_19_none_value_refused() -> None:
    with pytest.raises(ActionRefused) as refused:
        correct([details()], uuid.uuid4(), f"pay_stub:{DOC}:gross_pay", None)
    assert refused.value.code == "invalid_value"


def test_FR_002_19_invalid_new_value_refused() -> None:
    with pytest.raises(ActionRefused) as refused:
        correct([details()], uuid.uuid4(), f"pay_stub:{DOC}:gross_pay", "-5")
    assert refused.value.code == "invalid_value"


def test_FR_002_19_correction_records_old_and_new() -> None:
    action = uuid.uuid4()
    accepted = correct([details()], action, f"pay_stub:{DOC}:gross_pay", "3100.00")
    assert accepted is not None
    document_id, correction = accepted
    assert document_id == DOC
    assert (correction.old_value, correction.new_value) == ("3000.00", "3100.00")
    assert correction.action_id == action


@settings(max_examples=30)
@given(st.uuids())
def test_FR_002_19_replayed_action_returns_none(action: uuid.UUID) -> None:
    stored = details()
    ref = f"pay_stub:{DOC}:gross_pay"
    first = correct([stored], action, ref, "3100.00")
    assert first is not None
    replayed = stored.model_copy(update={"corrections": [first[1]]})
    assert correct([replayed], action, ref, "3200.00") is None


def test_FR_002_19_dismissal_of_unknown_finding_refused() -> None:
    with pytest.raises(ActionRefused) as refused:
        accept_dismissal(
            None,
            {},
            finding_id=uuid.uuid4(),
            reviewer_id=REVIEWER,
            reason="ok",
            submitted_at=NOW,
        )
    assert refused.value.code == "unknown_finding"


@settings(max_examples=30)
@given(st.uuids(), st.text(min_size=1, max_size=20), st.text(min_size=1, max_size=20))
def test_FR_002_19_dismissed_finding_keeps_first_dismissal(
    finding_id: uuid.UUID, first: str, second: str
) -> None:
    stored = result_with(finding_id)
    once = accept_dismissal(
        stored, {}, finding_id=finding_id, reviewer_id=REVIEWER, reason=first, submitted_at=NOW
    )
    twice = accept_dismissal(
        stored, once, finding_id=finding_id, reviewer_id=REVIEWER, reason=second, submitted_at=NOW
    )
    assert twice == once
    assert twice[finding_id].reason == first
