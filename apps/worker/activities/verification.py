"""`verify_documents` stub (001, research R1). 002 replaces the body with its agent call (TW-9).

Pure and idempotent (TW-11): every case's documents count as complete with nothing to review, so
the workflow moves straight on to determination.
"""

from __future__ import annotations

import structlog
from temporalio import activity

from packages.domain.case import VerificationOutcome, VerificationRequest

log = structlog.get_logger()


@activity.defn(name="verify_documents")
async def verify_documents(request: VerificationRequest) -> VerificationOutcome:
    log.info(
        "verify_documents.stub",
        case_id=str(request.case_id),
        workflow_id=activity.info().workflow_id,
    )
    return VerificationOutcome(complete=True, needs_review=False)
