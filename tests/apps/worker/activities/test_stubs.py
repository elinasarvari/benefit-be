from __future__ import annotations

import uuid
from datetime import date

import pytest
from temporalio.testing import ActivityEnvironment

from apps.worker.activities.determination import DeterminationActivities, FixedStubResults
from apps.worker.activities.verification import verify_documents
from packages.domain.case import (
    DenialReason,
    DeterminationOutcome,
    DeterminationRequest,
    VerificationRequest,
)

# Synthetic household IDs, one per stub result the seed covers.
H_ELIGIBLE = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
H_INELIGIBLE = uuid.UUID("00000000-0000-4000-8000-0000000000b2")
H_UNKNOWN = uuid.UUID("00000000-0000-4000-8000-0000000000c3")
CASE_ID = uuid.UUID("00000000-0000-4000-8000-0000000000c1")
AS_OF = date(2026, 3, 17)

SEEDED = FixedStubResults(
    {
        H_ELIGIBLE: DeterminationOutcome.eligible,
        H_INELIGIBLE: DeterminationOutcome.ineligible,
        H_UNKNOWN: DeterminationOutcome.cannot_determine,
    }
)


@pytest.mark.parametrize(
    ("household_id", "outcome", "reason_code", "missing_items"),
    [
        (H_ELIGIBLE, DeterminationOutcome.eligible, None, []),
        (H_INELIGIBLE, DeterminationOutcome.ineligible, DenialReason.income_over_limit, []),
        (H_UNKNOWN, DeterminationOutcome.cannot_determine, None, ["pay_stub"]),
    ],
)
async def test_FR_001_16_stub_determination_follows_household_stub_field(
    household_id: uuid.UUID,
    outcome: DeterminationOutcome,
    reason_code: DenialReason | None,
    missing_items: list[str],
) -> None:
    activities = DeterminationActivities(SEEDED)
    request = DeterminationRequest(case_id=CASE_ID, household_id=household_id, as_of_date=AS_OF)
    env = ActivityEnvironment()

    first = await env.run(activities.determine_eligibility, request)
    again = await env.run(activities.determine_eligibility, request)

    assert first.outcome is outcome
    assert first.reason_code == reason_code
    assert first.failed_rules == ([reason_code] if reason_code else [])
    assert first.missing_items == missing_items
    assert first.as_of_date == AS_OF
    assert again == first  # pure and idempotent (TW-11)


async def test_FR_001_16_stub_verification_is_complete_without_review() -> None:
    request = VerificationRequest(case_id=CASE_ID, household_id=H_ELIGIBLE)

    outcome = await ActivityEnvironment().run(verify_documents, request)

    assert outcome.complete is True
    assert outcome.needs_review is False
