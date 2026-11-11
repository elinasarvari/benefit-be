"""Pure case rules used by CaseWorkflow (FR-001-01, FR-001-02, FR-001-16, FR-001-22).

No I/O and no clock: every function takes the dates it needs, so the workflow stays deterministic
(TW-1) and the rules are unit-tested with properties (KD-3).
"""

from __future__ import annotations

import calendar
from collections.abc import Iterable
from datetime import date, timedelta

from packages.domain.case import CaseState, DenialReason
from packages.domain.case import allowed_actions_for as allowed_actions_for  # data table (BA-6)
from packages.domain.review import ReviewReason

DECISION_DAYS = 45
COVERAGE_MONTHS = 12

# Features whose transitions are live. 010 and 011 add theirs here when they land.
ACTIVE_FEATURES = frozenset({"001"})

# (from, to) -> feature that owns the transition (data-model.md, "State transitions").
TRANSITIONS: dict[tuple[CaseState, CaseState], str] = {
    (CaseState.submitted, CaseState.verifying): "001",
    (CaseState.verifying, CaseState.determining): "001",
    (CaseState.verifying, CaseState.awaiting_applicant): "011",
    (CaseState.verifying, CaseState.under_review): "010",
    (CaseState.awaiting_applicant, CaseState.verifying): "011",
    (CaseState.awaiting_applicant, CaseState.under_review): "011",
    (CaseState.determining, CaseState.decided): "001",
    (CaseState.determining, CaseState.under_review): "001",
    (CaseState.under_review, CaseState.decided): "001",
    (CaseState.under_review, CaseState.determining): "010",
    (CaseState.under_review, CaseState.awaiting_applicant): "010",
}

# FR-001-16: when several rules fail, the denial names income first.
_DENIAL_PRECEDENCE = (
    DenialReason.income_over_limit,
    DenialReason.other_rule_not_met,
    DenialReason.missing_information,
)


def can_transition(from_state: CaseState, to_state: CaseState) -> bool:
    """True only for table rows owned by a live feature. `decided` is terminal."""
    return TRANSITIONS.get((from_state, to_state)) in ACTIVE_FEATURES


def decision_deadline(received_on: date) -> date:
    """45 calendar days after receipt (FR-001-01)."""
    return received_on + timedelta(days=DECISION_DAYS)


def coverage_for(received_on: date) -> tuple[date, date]:
    """First day of the receipt month to the last day of the 12th month (FR-001-22)."""
    start = received_on.replace(day=1)
    months = start.year * 12 + start.month - 1 + COVERAGE_MONTHS - 1
    end_year, end_month = divmod(months, 12)
    end_month += 1
    end = date(end_year, end_month, calendar.monthrange(end_year, end_month)[1])
    return start, end


def denial_reason_for(
    reason: ReviewReason, failed_rules: Iterable[DenialReason | str]
) -> DenialReason:
    """Reason code of a denial (FR-001-16).

    A case that could not be determined is denied for missing information. Otherwise income over
    the limit wins over any other failed rule.
    """
    if reason is ReviewReason.cannot_determine:
        return DenialReason.missing_information
    failed = {str(rule) for rule in failed_rules}
    for candidate in _DENIAL_PRECEDENCE:
        if candidate.value in failed:
            return candidate
    return DenialReason.other_rule_not_met
