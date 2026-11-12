"""The verification branch as 011 will wire it, as a small test workflow (002, ADR-002-06).

Kept in its own module with workflow-safe imports only: Temporal's sandbox re-imports the module
that defines a workflow, and the end-to-end test module imports httpx (and with it urllib),
which the sandbox refuses.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from apps.worker.workflows.verification_branch import NextStep, next_step, review_cause
    from packages.domain.verification import VerificationRequest, VerificationResult


@workflow.defn(name="VerificationBranchProbe")
class VerificationBranchProbe:
    """Check, route, wait for the review when one is raised, then determine."""

    def __init__(self) -> None:
        self._progress: list[str] = []
        self._released = False

    @workflow.run
    async def run(self, request: VerificationRequest) -> list[str]:
        result = await workflow.execute_activity(
            "verify_documents",
            request,
            result_type=VerificationResult,
            start_to_close_timeout=timedelta(seconds=30),
        )
        step = next_step(result, request_already_sent=False)
        self._progress.append(f"step:{step.value}")
        if step is NextStep.verification_review:
            cause = review_cause(result)
            self._progress.append(f"cause:{cause.value if cause else 'none'}")
            await workflow.wait_condition(lambda: self._released)
        self._progress.append("determined")
        return self._progress

    @workflow.signal(name="review_done")
    def review_done(self) -> None:
        self._released = True

    @workflow.query(name="progress")
    def progress(self) -> list[str]:
        return list(self._progress)
