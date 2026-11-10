"""Activity option presets (TW-10, FR-000-13). Every activity call uses one of these.

Retry budget: an agent step is tried at most 3 times, and each try may make up to
1 + LLM_MAX_RETRIES model calls (the SDK retries inside the attempt), so a step makes at most
3 * (1 + LLM_MAX_RETRIES) model calls.

UNTIL_DONE is for the CaseWorkflow steps that must never fail the workflow on an outage
(`record_case_change`, `determine_eligibility`): a failed CaseWorkflow can't be restarted
(REJECT_DUPLICATE) and would leave the case open, locking the household out (ADR-001-02). The
server retries without limit, with backoff capped at 5 minutes, and a retry adds no history event,
so a long outage can't reach the history limit. Non-retryable errors and cancellation still end
the activity at once. Activities using it call `warn_if_retrying` so the outage shows in the logs.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TypedDict

import structlog
from temporalio import activity
from temporalio.common import RetryPolicy

log = structlog.get_logger()

NON_RETRYABLE_ERRORS = ["ValidationError", "PermissionDenied", "CredentialRejected"]


class ActivityOptions(TypedDict):
    start_to_close_timeout: timedelta
    retry_policy: RetryPolicy


DEFAULT: ActivityOptions = {
    "start_to_close_timeout": timedelta(seconds=30),
    "retry_policy": RetryPolicy(
        initial_interval=timedelta(seconds=1),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(seconds=30),
        maximum_attempts=5,
        non_retryable_error_types=NON_RETRYABLE_ERRORS,
    ),
}

AGENT_STEP: ActivityOptions = {
    "start_to_close_timeout": timedelta(seconds=120),
    "retry_policy": RetryPolicy(
        initial_interval=timedelta(seconds=2),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(seconds=30),
        maximum_attempts=3,
        non_retryable_error_types=NON_RETRYABLE_ERRORS,
    ),
}

UNTIL_DONE: ActivityOptions = {
    "start_to_close_timeout": timedelta(seconds=30),
    "retry_policy": RetryPolicy(
        initial_interval=timedelta(seconds=1),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(minutes=5),
        maximum_attempts=0,  # unlimited
        non_retryable_error_types=NON_RETRYABLE_ERRORS,
    ),
}

# Attempts after which an UNTIL_DONE activity logs each retry as a warning (an outage, not a blip).
WARN_AFTER_ATTEMPTS = 3


def warn_if_retrying() -> None:
    """Call first thing in an UNTIL_DONE activity. Logs IDs and the attempt number only (PL-11)."""
    info = activity.info()
    if info.attempt > WARN_AFTER_ATTEMPTS:
        log.warning(
            "activity.retrying",
            activity_type=info.activity_type,
            attempt=info.attempt,
            workflow_id=info.workflow_id,
        )
