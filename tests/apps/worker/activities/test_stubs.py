from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date

import pytest
from structlog.testing import capture_logs
from temporalio.testing import ActivityEnvironment

from apps.worker.activities.determination import DeterminationActivities, FixedStubResults
from apps.worker.activities.verification import verify_documents
from apps.worker.policies import WARN_AFTER_ATTEMPTS
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


async def test_FR_001_16_unseeded_household_is_cannot_determine() -> None:
    """R21: a household with no stub result fails closed to review; seeded H1 keeps `eligible`."""
    activities = DeterminationActivities(SEEDED)
    env = ActivityEnvironment()
    unseeded = uuid.UUID("00000000-0000-4000-8000-0000000000d4")

    result = await env.run(
        activities.determine_eligibility,
        DeterminationRequest(case_id=CASE_ID, household_id=unseeded, as_of_date=AS_OF),
    )
    seeded = await env.run(
        activities.determine_eligibility,
        DeterminationRequest(case_id=CASE_ID, household_id=H_ELIGIBLE, as_of_date=AS_OF),
    )

    assert result.outcome is DeterminationOutcome.cannot_determine
    assert result.missing_items == ["pay_stub"]
    assert seeded.outcome is DeterminationOutcome.eligible


async def test_FR_001_16_stub_verification_is_complete_without_review() -> None:
    request = VerificationRequest(case_id=CASE_ID, household_id=H_ELIGIBLE)

    outcome = await ActivityEnvironment().run(verify_documents, request)

    assert outcome.complete is True
    assert outcome.needs_review is False


@pytest.mark.parametrize("attempt", [1, WARN_AFTER_ATTEMPTS, WARN_AFTER_ATTEMPTS + 1, 40])
async def test_FR_001_03_outage_retry_logs_warning_with_attempt_only(attempt: int) -> None:
    """UNTIL_DONE retries are invisible in history, so later attempts warn in the log (BE-6)."""
    env = ActivityEnvironment()
    env.info = replace(env.info, workflow_id=f"case-{CASE_ID}", attempt=attempt)
    request = DeterminationRequest(case_id=CASE_ID, household_id=H_ELIGIBLE, as_of_date=AS_OF)

    with capture_logs() as logs:
        await env.run(DeterminationActivities(SEEDED).determine_eligibility, request)

    warnings = [entry for entry in logs if entry["log_level"] == "warning"]
    if attempt <= WARN_AFTER_ATTEMPTS:
        assert warnings == []
        return
    assert warnings == [
        {
            "event": "activity.retrying",
            "log_level": "warning",
            "activity_type": env.info.activity_type,
            "attempt": attempt,
            "workflow_id": f"case-{CASE_ID}",
        }
    ]  # IDs and the attempt number only, no personal data (PL-11)
