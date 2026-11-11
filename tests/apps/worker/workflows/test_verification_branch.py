"""FR-002-25: routing after the pay stub check."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from apps.worker.workflows.verification_branch import NextStep, next_step
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
def test_FR_002_25_next_step_after_the_check(
    findings: list[Finding], missing: bool, sent: bool, expected: NextStep
) -> None:
    result = _result(findings=findings, missing=missing)
    assert next_step(result, request_already_sent=sent) is expected
