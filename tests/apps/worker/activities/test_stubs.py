from __future__ import annotations

import uuid
from datetime import date

from temporalio.testing import ActivityEnvironment

from apps.worker.activities.verification import verify_documents
from packages.domain.case import VerificationRequest

# Synthetic IDs. The determination stub of 001 is gone: the real activity is tested in
# test_determination.py (ADR-003-02).
HOUSEHOLD = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
CASE_ID = uuid.UUID("00000000-0000-4000-8000-0000000000c1")
AS_OF = date(2026, 3, 17)


async def test_FR_001_16_stub_verification_is_complete_without_review() -> None:
    request = VerificationRequest(case_id=CASE_ID, household_id=HOUSEHOLD)

    outcome = await ActivityEnvironment().run(verify_documents, request)

    assert outcome.complete is True
    assert outcome.needs_review is False
