"""`verify_documents` stub (001, research R1). 002 replaces the body with its agent call (TW-9).

Pure and idempotent (TW-11): every case's documents count as complete with nothing to review, so
the workflow moves straight on to determination. `identity_verified` stays None: the stub does
not check identity, so the rules keep reading the seeded verifications (ADR-003-03).
"""

from __future__ import annotations

import structlog
from temporalio import activity

from packages.domain.verification import VerificationRequest, VerificationResult

log = structlog.get_logger()


@activity.defn(name="verify_documents")
async def verify_documents(request: VerificationRequest) -> VerificationResult:
    log.info(
        "verify_documents.stub",
        case_id=str(request.case_id),
        workflow_id=activity.info().workflow_id,
    )
    return VerificationResult(complete=True, needs_review=False, case_id=request.case_id)
