"""FR-002-25: routing after the pay stub check."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from apps.worker.workflows.verification_branch import NextStep, next_step, review_cause
from packages.domain.review import ReviewCause
from packages.domain.verification import (
    Dismissal,
    Finding,
    FindingCause,
    MissingDocument,
    MissingDocumentKind,
    VerificationResult,
)


def _finding(*, dismissed: bool = False) -> Finding:
    return Finding(
        finding_id=uuid4(),
        cause=FindingCause.income_mismatch,
        dismissed=(
            Dismissal(reviewer_id=uuid4(), reason="checked", dismissed_at=datetime.now(UTC))
            if dismissed
            else None
        ),
    )


def _result(*, findings: list[Finding], missing: bool) -> VerificationResult:
    items = [MissingDocument(member_id=uuid4(), job_id=uuid4(), kind=MissingDocumentKind.no_stub)]
    return VerificationResult(
        complete=not missing,
        needs_review=bool(findings),
        findings=findings,
        missing_items=items if missing else [],
    )


@pytest.mark.parametrize(
    ("findings", "missing", "sent", "expected"),
    [
        ([_finding()], True, False, NextStep.verification_review),
        ([_finding(dismissed=True)], True, False, NextStep.request_information),
        ([], True, False, NextStep.request_information),
        ([], True, True, NextStep.determine),
        ([], False, False, NextStep.determine),
    ],
)
def test_FR_002_25_routing_order_all_four_outcomes(
    findings: list[Finding], missing: bool, sent: bool, expected: NextStep
) -> None:
    """An open finding beats missing items; a dismissed one does not."""
    result = _result(findings=findings, missing=missing)
    assert next_step(result, request_already_sent=sent) is expected


def test_FR_002_25_routing_order_all_four_outcomes_are_covered() -> None:
    cases = [
        (_result(findings=[_finding()], missing=True), False),
        (_result(findings=[], missing=True), False),
        (_result(findings=[], missing=True), True),
        (_result(findings=[], missing=False), False),
    ]
    assert {next_step(r, request_already_sent=sent) for r, sent in cases} == set(NextStep)


@pytest.mark.parametrize("cause", list(FindingCause))
def test_FR_002_16_review_cause_per_finding_kind(cause: FindingCause) -> None:
    finding = Finding(finding_id=uuid4(), cause=cause)
    assert review_cause(_result(findings=[finding], missing=False)) is ReviewCause(cause.value)


def test_FR_002_16_review_cause_none_when_no_finding_is_open() -> None:
    assert review_cause(_result(findings=[], missing=True)) is None
    assert review_cause(_result(findings=[_finding(dismissed=True)], missing=False)) is None


def test_FR_002_16_review_cause_is_the_first_open_finding() -> None:
    dismissed = _finding(dismissed=True)
    second = Finding(finding_id=uuid4(), cause=FindingCause.different_person)
    third = Finding(finding_id=uuid4(), cause=FindingCause.identity_mismatch)
    result = _result(findings=[dismissed, second, third], missing=False)
    assert review_cause(result) is ReviewCause.different_person
