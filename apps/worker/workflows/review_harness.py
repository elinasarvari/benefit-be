"""A minimal workflow that composes `review.py` until 001's `CaseWorkflow` exists (plan "Open
questions"). It raises each review reason the way `CaseWorkflow` will (T013) and is kept for tests
once `CaseWorkflow` lands (T018).

Workflow ID: `case-{case_id}` (TW-3).
"""

from __future__ import annotations

from collections import deque
from datetime import date, timedelta
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from apps.worker.workflows.review import (
        DETERMINATION_REF_KIND,
        EXPLANATION_REF_KIND,
        FINDING_REF_KIND,
        NOTICE_DRAFT_REF_KIND,
        REQUEST_REF_KIND,
        ReviewContext,
        ReviewHandlers,
        ReviewLoop,
    )
    from apps.worker.workflows.review_ports import (
        AdvanceClock,
        EligibilityOutcome,
        EvaluationResult,
        RecordedTransitions,
        ReviewResult,
        StubCaseClock,
    )
    from apps.worker.workflows.review_timers import ReviewWindowSettings
    from packages.domain.review import (
        CaseworkerActionSignal,
        EvidenceRef,
        ReviewCause,
        ReviewReason,
    )


class HarnessScenario(StrEnum):
    determine = "determine"  # evaluate: Ineligible / Cannot determine / failure
    verify = "verify"  # verify_documents: findings / failure
    notice_ready = "notice_ready"
    missed_response_window = "missed_response_window"
    explanation_check_failed = "explanation_check_failed"


class ReviewHarnessInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    household_id: UUID
    case_id: UUID
    decision_deadline: date
    scenario: HarnessScenario
    # The notice draft, request or explanation the scenario is about.
    subject_id: UUID
    coverage_end: date | None = None
    windows: ReviewWindowSettings = Field(default_factory=ReviewWindowSettings)
    # Case-clock speed: 1 normally, 1440 in the demo profile (FR-011-04).
    time_scale: float = Field(default=1.0, gt=0)
    # Tests that signal after the last task closes keep the workflow open until `finish`.
    wait_for_finish: bool = False


class ReviewHarnessResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[ReviewResult]
    determination: EvaluationResult | None
    auto_rfi_used: bool
    progress: list[str]


@workflow.defn(name="ReviewHarnessWorkflow")
class ReviewHarnessWorkflow(ReviewHandlers):
    def __init__(self) -> None:
        self.review_queue: deque[CaseworkerActionSignal] = deque()
        self.review: ReviewLoop | None = None
        self._clock: StubCaseClock | None = None
        self._transitions = RecordedTransitions()
        self._progress: list[str] = []
        self._finished = False

    @workflow.run
    async def run(self, data: ReviewHarnessInput) -> ReviewHarnessResult:
        self._clock = StubCaseClock(data.time_scale)
        review = ReviewLoop(
            ReviewContext(
                household_id=data.household_id,
                case_id=data.case_id,
                workflow_id=workflow.info().workflow_id,
                decision_deadline=data.decision_deadline,
            ),
            data.windows,
            self._clock,
            self._transitions,
            self.review_queue,
        )
        self.review = review
        return await review.alongside(self._drive(review, data))

    async def _drive(self, review: ReviewLoop, data: ReviewHarnessInput) -> ReviewHarnessResult:
        determination = await self._raise(review, data)
        self._progress.append(f"{data.scenario.value}:continued")

        await workflow.wait_condition(
            lambda: (
                not review.open_tasks()
                and review.idle
                and (self._finished or not data.wait_for_finish)
            )
        )
        return ReviewHarnessResult(
            results=self._transitions.results,
            determination=determination,
            auto_rfi_used=review.state.auto_rfi_used,
            progress=self._progress,
        )

    async def _raise(self, review: ReviewLoop, data: ReviewHarnessInput) -> EvaluationResult | None:
        """The raising points for each reason, with its cause and evidence (FR-010-01, T013)."""
        match data.scenario:
            case HarnessScenario.determine:
                determination = await review.evaluate()
                if determination is not None:
                    refs = [
                        EvidenceRef(kind=DETERMINATION_REF_KIND, id=determination.determination_id)
                    ]
                    if determination.outcome is EligibilityOutcome.ineligible:
                        await review.open_review(
                            ReviewReason.adverse_determination, ReviewCause.ineligible, refs
                        )
                    elif determination.outcome is EligibilityOutcome.cannot_determine:
                        await review.open_review(
                            ReviewReason.cannot_determine, ReviewCause.cannot_determine, refs
                        )
                return determination
            case HarnessScenario.verify:
                verification = await review.verify_documents()
                for finding in verification.findings if verification is not None else []:
                    if finding.missing_information:
                        await review.request_information_automatically(finding)
                    else:
                        await review.open_review(
                            ReviewReason.verification_review,
                            finding.cause,
                            [EvidenceRef(kind=FINDING_REF_KIND, id=finding.finding_id)],
                        )
            case HarnessScenario.notice_ready:
                await review.open_review(
                    ReviewReason.notice_approval,
                    ReviewCause.notice_ready,
                    [EvidenceRef(kind=NOTICE_DRAFT_REF_KIND, id=data.subject_id)],
                )
            case HarnessScenario.missed_response_window:
                await review.open_review(
                    ReviewReason.missed_response_deadline,
                    ReviewCause.response_deadline_missed,
                    [EvidenceRef(kind=REQUEST_REF_KIND, id=data.subject_id)],
                    coverage_end=data.coverage_end,
                )
            case HarnessScenario.explanation_check_failed:
                await review.open_review(
                    ReviewReason.explanation_review,
                    ReviewCause.explanation_check_failed,
                    [EvidenceRef(kind=EXPLANATION_REF_KIND, id=data.subject_id)],
                )
        return None

    @workflow.signal(name="advance_clock")
    def advance_clock(self, data: AdvanceClock) -> None:
        """Stand-in for 011's advance-clock signal (FR-011-05)."""
        if self._clock is not None and self.review is not None:
            self._clock.advance(timedelta(days=data.days))
            self.review.clock_changed()

    @workflow.signal(name="finish")
    def finish(self) -> None:
        self._finished = True

    @workflow.query(name="progress")
    def progress(self) -> list[str]:
        return list(self._progress)
