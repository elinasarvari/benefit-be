"""Activity option presets (TW-10, FR-000-13). Every activity call uses one of these.

Retry budget: an agent step is tried at most 3 times, and each try may make up to
1 + LLM_MAX_RETRIES model calls (the SDK retries inside the attempt), so a step makes at most
3 * (1 + LLM_MAX_RETRIES) model calls.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TypedDict

from temporalio.common import RetryPolicy

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

# Determination by the rules engine (ADR-010-04): 3 attempts, then the case opens a review task.
RULES_STEP: ActivityOptions = {
    "start_to_close_timeout": timedelta(seconds=60),
    "retry_policy": RetryPolicy(
        initial_interval=timedelta(seconds=1),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(seconds=30),
        maximum_attempts=3,
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
