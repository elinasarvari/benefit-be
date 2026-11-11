"""Completed history loaded straight into the stores (FR-003-22, ADR-003-06).

For a fixed sample of the non-demo households: one decided case from the previous program year
and one decided case of the current period, each with its snapshot, determination (the real
engine's output), events, review task and caseworker action where the result was not eligible,
approval or denial outbox entry, and coverage period where approved, all in main's shapes.
Pre-written explanations and notices go to `generated/history/` as files keyed by
`determination_id` and `case_id`: their stores belong to 004 and 011 (ADR-003-06).

Demo households A, B and C get nothing here: they start with no open case and no coverage
(FR-003-14). Every date is an offset from the dataset's anchor date, never the wall clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID, uuid5

from db.seeds.households import YEARS, SeededDataset, SeededHousehold, snapshot_for
from db.seeds.ids import HOUSEHOLD_A, HOUSEHOLD_B, HOUSEHOLD_C, rng, seeded_id
from db.seeds.records import to_document
from packages.domain.auth import Role
from packages.domain.case import (
    ActionOutcome,
    ActionType,
    Case,
    CaseClock,
    CaseEvent,
    CaseEventKind,
    CaseOutcome,
    CaseOverride,
    CaseState,
    CaseType,
    CaseworkerAction,
    CoveragePeriod,
    CoverageStatus,
    DenialReason,
    DeterminationOutcome,
    EventActorType,
    NoticeRequest,
    NoticeType,
    ReviewReason,
    ReviewTask,
    ReviewTaskStatus,
    case_id_for,
)
from packages.domain.eligibility import Determination, DeterminationRecord, RuleId, RuleOutcome
from packages.domain.review import EvidenceRef, ReviewCause
from packages.rules import evaluate

HISTORY_HOUSEHOLDS = 60
DEADLINE_DAYS = 45
COVERAGE_DAYS = 364
DEMO_WINDOW_DAYS = 90  # coverage ending within this many days of the anchor is "due" (007)
REDETERMINATION_LEAD_DAYS = 60  # FR-007-01
CASEWORKER = seeded_id("user-caseworker1")
SYNTHETIC_REASON = "SYNTHETIC override: documents reviewed by the seeded caseworker."
DEMO = frozenset({HOUSEHOLD_A, HOUSEHOLD_B, HOUSEHOLD_C})

COLLECTIONS = (
    "cases",
    "case_events",
    "household_snapshots",
    "determinations",
    "coverage_periods",
    "review_tasks",
    "caseworker_actions",
    "outbox",
)


@dataclass(frozen=True)
class History:
    documents: dict[str, list[dict[str, Any]]]
    explanations: dict[UUID, dict[str, Any]] = field(default_factory=dict)
    notices: dict[tuple[UUID, str], dict[str, Any]] = field(default_factory=dict)
    due: list[dict[str, Any]] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {name: len(docs) for name, docs in self.documents.items()}


def determination_id_for(case_id: UUID, as_of: date) -> UUID:
    """The formula the worker's determination activity uses (research R5)."""
    return uuid5(case_id, f"determination:{as_of.isoformat()}")


def snapshot_id_for(case_id: UUID, as_of: date) -> UUID:
    return uuid5(case_id, f"snapshot:{as_of.isoformat()}")


def _at(day: date, hour: int = 15) -> datetime:
    return datetime.combine(day, time(hour), tzinfo=UTC)


def _marked(model: Any, id_field: str | None) -> dict[str, Any]:
    document = to_document(model, id_field)
    document["synthetic"] = True
    return document


@dataclass(frozen=True)
class _Plan:
    household: SeededHousehold
    label: str
    decided_offset: int  # decided date = anchor + offset
    review_extra: int


def _plans(dataset: SeededDataset) -> list[_Plan]:
    pool = [h for h in dataset.households if h.household_id not in DEMO]
    chosen = sorted(
        rng("history:households").sample(range(len(pool)), HISTORY_HOUSEHOLDS),
    )
    plans = []
    for k, index in enumerate(chosen):
        household = pool[index]
        prior_decided = -590 + (k * 7) % 60
        if k % 4 == 0:  # coverage ends inside the demo window, so 007 finds the household
            current_decided = -290 + (k // 4 * 2) % 17
        else:
            current_decided = -270 + (k * 11) % 200
        for label, decided in (("prior", prior_decided), ("current", current_decided)):
            plans.append(_Plan(household, label, decided, k % 6))
    return plans


def build_history(dataset: SeededDataset) -> History:
    docs: dict[str, list[dict[str, Any]]] = {name: [] for name in COLLECTIONS}
    explanations: dict[UUID, dict[str, Any]] = {}
    notices: dict[tuple[UUID, str], dict[str, Any]] = {}
    due: list[dict[str, Any]] = []
    anchor = dataset.anchor
    for plan in _plans(dataset):
        household = plan.household
        reference = f"seed-history-{household.seed_key}-{plan.label}"
        case_id = case_id_for(reference)
        decided_on = anchor + timedelta(days=plan.decided_offset)
        # Engine first: a review path takes longer, so the case was received earlier.
        year_guess = decided_on.year if decided_on.year in YEARS else YEARS[-1]
        probe_received = decided_on - timedelta(days=3 + plan.review_extra)
        year = probe_received.year if probe_received.year in YEARS else year_guess
        probe = evaluate(snapshot_for(household, year, as_of_date=probe_received), probe_received)
        review = probe.result is not DeterminationOutcome.eligible
        received = decided_on - timedelta(days=3 + plan.review_extra + (9 if review else 0))
        as_of = received
        year = as_of.year if as_of.year in YEARS else year_guess
        snapshot = snapshot_for(household, year, case_id=case_id, as_of_date=as_of).model_copy(
            update={
                "snapshot_id": snapshot_id_for(case_id, as_of),
                "captured_at": _at(received, 16),
            }
        )
        determination = evaluate(snapshot, as_of)
        determination_id = determination_id_for(case_id, as_of)
        record = DeterminationRecord(
            determination_id=determination_id,
            household_id=household.household_id,
            case_id=case_id,
            snapshot_id=snapshot.snapshot_id,
            recorded_at=_at(received + timedelta(days=1), 10),
            determination=determination,
        )
        docs["household_snapshots"].append(_marked(snapshot, "snapshot_id"))
        docs["determinations"].append(_marked(record, "determination_id"))

        parts = _decide(
            household.household_id,
            case_id,
            reference,
            received,
            decided_on,
            determination,
            determination_id,
            override=review
            and determination.result is DeterminationOutcome.ineligible
            and plan.review_extra == 0,
            label=plan.label,
        )
        for name, items in parts.items():
            docs[name].extend(items)
        case = parts["cases"][0]
        explanations[determination_id] = _explanation(
            household.household_id, case_id, determination_id, determination
        )
        notice_type = NoticeType.approval if case["outcome"] == "approved" else NoticeType.denial
        notices[(case_id, notice_type.value)] = {
            "case_id": str(case_id),
            "household_id": str(household.household_id),
            "notice_type": notice_type.value,
            "text": _notice_text(notice_type, determination),
            "synthetic": True,
        }
        for period in parts["coverage_periods"]:
            end = period["end_date"].date()
            if period["status"] == CoverageStatus.active.value and (
                0 <= (end - anchor).days <= DEMO_WINDOW_DAYS
            ):
                due.append(
                    {
                        "household_id": household.household_id,
                        "coverage_period_id": period["_id"],
                        "coverage_end": end,
                        "redetermination_start": end - timedelta(days=REDETERMINATION_LEAD_DAYS),
                        "household": household,
                    }
                )
    return History(documents=docs, explanations=explanations, notices=notices, due=due)


def _decide(
    household_id: UUID,
    case_id: UUID,
    reference: str,
    received: date,
    decided_on: date,
    determination: Determination,
    determination_id: UUID,
    *,
    override: bool,
    label: str,
) -> dict[str, list[dict[str, Any]]]:
    t0 = _at(received)
    t_dec = _at(decided_on)
    result = determination.result
    failed = {r.rule_id for r in determination.rule_results if r.outcome is RuleOutcome.fail}
    review = result is not DeterminationOutcome.eligible
    approved = result is DeterminationOutcome.eligible or override

    states: list[tuple[CaseEventKind, CaseState | None, CaseState | None, str, bool]] = [
        (
            CaseEventKind.state_changed,
            CaseState.submitted,
            CaseState.verifying,
            "verification_started",
            False,
        ),
        (
            CaseEventKind.state_changed,
            CaseState.verifying,
            CaseState.determining,
            "verification_complete",
            False,
        ),
    ]
    task: ReviewTask | None = None
    action: CaseworkerAction | None = None
    if review:
        cause = (
            ReviewCause.ineligible
            if result is DeterminationOutcome.ineligible
            else ReviewCause.cannot_determine
        )
        reason = (
            ReviewReason.adverse_determination
            if result is DeterminationOutcome.ineligible
            else ReviewReason.cannot_determine
        )
        task_id = uuid5(case_id, "task-1")
        action_id = uuid5(case_id, "action-1")
        opened = t0 + timedelta(days=2)
        acted = t_dec - timedelta(hours=2)
        deadline = received + timedelta(days=DEADLINE_DAYS)
        task = ReviewTask(
            task_id=task_id,
            household_id=household_id,
            case_id=case_id,
            workflow_id=f"case-{case_id}",
            reason=reason,
            cause=cause,
            evidence_refs=[EvidenceRef(kind="determination", id=determination_id)],
            blocking=True,
            status=ReviewTaskStatus.closed,
            opened_at_case=opened,
            opened_at_real=opened,
            decision_deadline=deadline,
            closed_by_action_id=action_id,
            created_at=opened,
            updated_at=acted,
        )
        action = CaseworkerAction(
            action_id=action_id,
            task_id=task_id,
            action=ActionType.override_to_approve if override else ActionType.approve_denial,
            reviewer_id=CASEWORKER,
            reviewer_role=Role.caseworker,
            reason=SYNTHETIC_REASON if override else None,
            submitted_at=acted,
            household_id=household_id,
            case_id=case_id,
            outcome=ActionOutcome.applied,
            applied_at_case=acted,
            recorded_at=acted,
        )
        states += [
            (
                CaseEventKind.state_changed,
                CaseState.determining,
                CaseState.under_review,
                "determination_adverse"
                if cause is ReviewCause.ineligible
                else "determination_cannot_determine",
                False,
            ),
            (CaseEventKind.review_task_opened, None, None, "review_task_opened", False),
            (
                CaseEventKind.caseworker_action,
                None,
                None,
                "override_to_approve" if override else "approve_denial",
                True,
            ),
            (
                CaseEventKind.state_changed,
                CaseState.under_review,
                CaseState.decided,
                "override_approved" if override else "denial_approved",
                True,
            ),
        ]
    else:
        states.append(
            (
                CaseEventKind.state_changed,
                CaseState.determining,
                CaseState.decided,
                "determined_eligible",
                False,
            )
        )
    notice_type = NoticeType.approval if approved else NoticeType.denial
    states.append(
        (CaseEventKind.notice_requested, None, None, f"{notice_type.value}_notice_requested", False)
    )
    coverage: CoveragePeriod | None = None
    if approved:
        states.append((CaseEventKind.coverage_started, None, None, "coverage_started", False))
        coverage = CoveragePeriod(
            id=uuid5(case_id, "coverage"),
            household_id=household_id,
            case_id=case_id,
            start_date=decided_on,
            end_date=decided_on + timedelta(days=COVERAGE_DAYS),
            status=CoverageStatus.ended if label == "prior" else CoverageStatus.active,
        )

    events = []
    span = (t_dec - t0).total_seconds()
    for seq, (kind, from_state, to_state, code, by_user) in enumerate(states, start=1):
        when = t0 + timedelta(seconds=span * min(seq, len(states)) / len(states))
        if kind in (CaseEventKind.notice_requested, CaseEventKind.coverage_started) or (
            to_state is CaseState.decided
        ):
            when = t_dec + timedelta(minutes=seq)
        events.append(
            CaseEvent(
                id=uuid5(case_id, f"event-{seq}"),
                case_id=case_id,
                household_id=household_id,
                seq=seq,
                kind=kind,
                from_state=from_state,
                to_state=to_state,
                case_time=when,
                real_time=when,
                actor_type=EventActorType.user if by_user else EventActorType.system,
                actor_id=CASEWORKER if by_user else None,
                reason_code=code,
                review_task_id=task.task_id
                if task and kind is CaseEventKind.review_task_opened
                else None,
                created_at=when,
            )
        )

    if result is DeterminationOutcome.eligible or override:
        outcome, denial = CaseOutcome.approved, None
    else:
        outcome = CaseOutcome.denied
        denial = (
            DenialReason.income_over_limit
            if failed == {RuleId.ELIG_06}
            else DenialReason.other_rule_not_met
            if result is DeterminationOutcome.ineligible
            else DenialReason.missing_information
        )
    case = Case(
        id=case_id,
        household_id=household_id,
        case_id=case_id,
        case_type=CaseType.application,
        submission_reference=reference,
        state=CaseState.decided,
        open=False,
        received_on=received,
        decision_deadline=received + timedelta(days=DEADLINE_DAYS),
        clock=CaseClock(real_start=t0, case_start=t0, scale=1),
        outcome=outcome,
        denial_reason=denial,
        override=CaseOverride(
            reviewer_id=CASEWORKER, reason=SYNTHETIC_REASON, determination_id=determination_id
        )
        if override
        else None,
        decided_on=decided_on,
        last_seq=len(events),
        created_at=t0,
        updated_at=t_dec,
    )
    notice = NoticeRequest(
        id=uuid5(case_id, notice_type.value),
        case_id=case_id,
        household_id=household_id,
        notice_type=notice_type,
        requested_case_time=t_dec,
    )
    return {
        "cases": [_marked(case, "id")],
        "case_events": [_marked(e, "id") for e in events],
        "coverage_periods": [_marked(coverage, "id")] if coverage else [],
        "review_tasks": [_review_task_document(task)] if task else [],
        "caseworker_actions": [_action_document(action)] if action else [],
        "outbox": [_marked(notice, "id")],
    }


def _review_task_document(task: ReviewTask) -> dict[str, Any]:
    document = _marked(task, "task_id")
    document["task_id"] = task.task_id
    return document


def _action_document(action: CaseworkerAction) -> dict[str, Any]:
    document = _marked(action, "action_id")
    document["action_id"] = action.action_id
    return document


def _explanation(
    household_id: UUID, case_id: UUID, determination_id: UUID, determination: Determination
) -> dict[str, Any]:
    refs = [r.policy_ref for r in determination.rule_results]
    if determination.result is DeterminationOutcome.eligible:
        text = (
            f"Your household of {determination.household_size} meets every rule: "
            f"monthly income {determination.household_monthly_income} is within the limit "
            f"{determination.monthly_income_limit}."
        )
    elif determination.result is DeterminationOutcome.ineligible:
        failed = ", ".join(
            r.rule_id.value for r in determination.rule_results if r.outcome is RuleOutcome.fail
        )
        text = f"Your household did not meet these rules: {failed}."
    else:
        items = ", ".join(sorted({m.item for m in determination.missing_items}))
        text = f"We could not decide because this information is missing: {items}."
    return {
        "determination_id": str(determination_id),
        "case_id": str(case_id),
        "household_id": str(household_id),
        "result": determination.result.value,
        "text": f"SYNTHETIC explanation. {text}",
        "citations": refs,
        "rule_ids": [r.rule_id.value for r in determination.rule_results],
        "synthetic": True,
    }


def _notice_text(notice_type: NoticeType, determination: Determination) -> str:
    if notice_type is NoticeType.approval:
        return "SYNTHETIC approval notice: your coverage has been approved."
    return f"SYNTHETIC denial notice: your application was denied ({determination.result.value})."
