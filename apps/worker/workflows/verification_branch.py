"""What the case does after the pay stub check (FR-002-25). Pure: no I/O, no clock (TW-1).

Owned by 002; 011 calls it from `case.py` when it wires the request for information.
"""

from __future__ import annotations

from enum import StrEnum

from packages.domain.review import ReviewCause
from packages.domain.verification import VerificationResult


class NextStep(StrEnum):
    verification_review = "verification_review"
    request_information = "request_information"
    determine = "determine"


def next_step(result: VerificationResult, *, request_already_sent: bool) -> NextStep:
    """Open findings go to review first. Missing information gets the case's one automatic
    request (FR-010-04); after that the rules return "Cannot determine", which goes to review."""
    if any(finding.dismissed is None for finding in result.findings):
        return NextStep.verification_review
    if result.missing_items and not request_already_sent:
        return NextStep.request_information
    return NextStep.determine


def review_cause(result: VerificationResult) -> ReviewCause | None:
    """The cause of the "Verification review" task: the first open finding's (FR-002-16).
    None when no finding is open."""
    for finding in result.findings:
        if finding.dismissed is None:
            return ReviewCause(finding.cause.value)
    return None
