"""The pay stub check as activities (FR-002-03, FR-002-19, FR-002-23; ADR-002-02, -04, -06).

`verify_documents` is thin (TW-9): load the members, the entered stubs and the reference lookup,
call the pure `check_pay_stubs`, store the result. The same case gives the same result and one
stored row (TW-11). `apply_verification_action` records a caseworker's correction or dismissal once
per `action_id` (the correction and dismissal times are the action's own `submitted_at`, so a replay
writes nothing new) and re-runs the check. Errors the caller cannot fix carry `ValidationError` and
never the reason text (PL-11).

The module-level `verify_documents` is 001's stub, kept so harnesses that import it still load;
the worker registers `VerificationActivities.verify_documents` instead.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from uuid import UUID

import structlog
from temporalio import activity
from temporalio.exceptions import ApplicationError

from apps.worker.repositories.case_members import CaseMembers
from apps.worker.repositories.pay_stubs import PayStubStore
from apps.worker.repositories.reference_records import ReferenceRecords
from packages.domain.review import ActionType, CaseworkerAction
from packages.domain.verification import (
    Dismissal,
    IdentityMatch,
    ReferenceAnswer,
    ReferenceLookup,
    VerificationRequest,
    VerificationResult,
    effective,
)
from packages.platform.clock import Clock, today
from packages.platform.scope import Scope
from packages.rules.verification import (
    CheckInput,
    DeclaredJob,
    DeclaredMember,
    check_pay_stubs,
    declared_monthly,
    stub_assignments,
)
from packages.rules.verification_actions import ActionRefused, accept_correction, accept_dismissal

log = structlog.get_logger()

Check = Callable[..., VerificationResult]


def _reject(code: str) -> ApplicationError:
    """A refusal the workflow must not retry. The message is a code, never user text."""
    return ApplicationError(code, type="ValidationError", non_retryable=True)


def _dismissals(result: VerificationResult | None) -> dict[UUID, Dismissal]:
    if result is None:
        return {}
    return {f.finding_id: f.dismissed for f in result.findings if f.dismissed is not None}


class VerificationActivities:
    """Built once per worker process (TW-12) with its stores injected."""

    def __init__(
        self,
        members: CaseMembers,
        store: PayStubStore,
        references: ReferenceRecords,
        clock: Clock,
        threshold_percent: Decimal,
        check: Check = check_pay_stubs,
    ) -> None:
        self._members = members
        self._store = store
        self._references = references
        self._clock = clock
        self._threshold = threshold_percent
        self._check = check

    @activity.defn(name="verify_documents")
    async def verify_documents(self, request: VerificationRequest) -> VerificationResult:
        scope = Scope.for_worker(request.case_id, request.household_id)
        as_of = request.as_of_date or today(self._clock)
        dismissed = _dismissals(await self._store.stored_result(scope))
        result = await self._run(scope, as_of, dismissed)
        log.info(
            "verify_documents",
            case_id=str(request.case_id),
            workflow_id=activity.info().workflow_id,
            findings=len(result.findings),
            missing=len(result.missing_items),
        )
        return result

    @activity.defn(name="apply_verification_action")
    async def apply_verification_action(self, action: CaseworkerAction) -> VerificationResult:
        reason = (action.reason or "").strip()
        if not reason:
            raise _reject("reason_required")
        scope = Scope.for_worker(action.case_id, action.household_id)
        as_of = await self._store.stored_as_of_date(scope) or today(self._clock)
        stored = await self._store.stored_result(scope)
        dismissed = _dismissals(stored)
        if action.action is ActionType.correct_value:
            await self._correct(scope, action, reason)
        elif action.action is ActionType.dismiss_finding:
            dismissed = self._dismiss(stored, dismissed, action, reason)
        else:
            raise _reject("unsupported_action")
        result = await self._run(scope, as_of, dismissed)
        log.info(
            "apply_verification_action",
            case_id=str(action.case_id),
            action_id=str(action.action_id),
            workflow_id=activity.info().workflow_id,
        )
        return result

    async def _run(
        self, scope: Scope, as_of_date: date, dismissed: dict[UUID, Dismissal]
    ) -> VerificationResult:
        assert scope.case_id is not None and scope.household_id is not None  # noqa: S101
        members = await self._members.for_case(scope, as_of_date)
        stubs = [effective(d) for d in await self._store.details(scope)]
        references = {}
        for member in members:
            if not member.family_name:  # one-word name: the lookup needs both names (no record)
                references[member.member_id] = ReferenceAnswer(
                    member_id=member.member_id, identity=IdentityMatch.no_record
                )
                continue
            references[member.member_id] = await self._references.lookup(
                scope,
                ReferenceLookup(
                    household_id=scope.household_id,
                    member_id=member.member_id,
                    given_name=member.given_name,
                    family_name=member.family_name,
                    date_of_birth=member.date_of_birth,
                ),
            )
        data = CheckInput(
            case_id=scope.case_id,
            as_of_date=as_of_date,
            members=[
                DeclaredMember(
                    member_id=m.member_id,
                    full_name=m.full_name,
                    zero_income=m.zero_income,
                    jobs=[
                        DeclaredJob(
                            job_id=j.job_id,
                            employer=j.employer,
                            declared_monthly=declared_monthly(j.gross, j.frequency, as_of_date),
                        )
                        for j in m.jobs
                    ],
                )
                for m in members
            ],
            stubs=stubs,
            references=references,
            dismissed=dismissed,
        )
        result = self._check(data, threshold_percent=self._threshold)
        await self._store.save_result(
            scope, result, as_of_date, self._clock.now(), stub_assignments(data.members, data.stubs)
        )
        return result

    @staticmethod
    def _dismiss(
        stored: VerificationResult | None,
        dismissed: dict[UUID, Dismissal],
        action: CaseworkerAction,
        reason: str,
    ) -> dict[UUID, Dismissal]:
        if action.finding_id is None:
            raise _reject("missing_required_field")
        try:
            return accept_dismissal(
                stored,
                dismissed,
                finding_id=action.finding_id,
                reviewer_id=action.reviewer_id,
                reason=reason,
                submitted_at=action.submitted_at,
            )
        except ActionRefused as refused:
            raise _reject(refused.code) from None

    async def _correct(self, scope: Scope, action: CaseworkerAction, reason: str) -> None:
        if action.corrected_value is None:
            raise _reject("missing_required_field")
        try:
            accepted = accept_correction(
                await self._store.details(scope),
                action_id=action.action_id,
                field_ref=action.corrected_value.field_ref,
                value=action.corrected_value.value,
                reviewer_id=action.reviewer_id,
                reason=reason,
                submitted_at=action.submitted_at,
            )
        except ActionRefused as refused:
            raise _reject(refused.code) from None
        if accepted is not None:
            await self._store.append_correction(scope, *accepted)


@activity.defn(name="verify_documents")
async def verify_documents(request: VerificationRequest) -> VerificationResult:
    """001's stub: every case counts as complete with nothing to review."""
    log.info(
        "verify_documents.stub",
        case_id=str(request.case_id),
        workflow_id=activity.info().workflow_id,
    )
    return VerificationResult(complete=True, needs_review=False, case_id=request.case_id)
