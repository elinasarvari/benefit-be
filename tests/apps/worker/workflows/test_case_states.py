from __future__ import annotations

from datetime import date, timedelta
from itertools import product

import pytest
from hypothesis import given
from hypothesis import strategies as st

from apps.worker.workflows.case_states import (
    allowed_actions_for,
    can_transition,
    coverage_for,
    decision_deadline,
    denial_reason_for,
)
from packages.domain.case import ActionType, CaseState, DenialReason, ReviewReason

S = CaseState
ALLOWED_IN_001 = {
    (S.submitted, S.verifying),
    (S.verifying, S.determining),
    (S.determining, S.decided),
    (S.determining, S.under_review),
    (S.under_review, S.decided),
}
DATES = st.dates(min_value=date(2020, 1, 1), max_value=date(2040, 12, 31))


@pytest.mark.parametrize(
    ("received_on", "expected"),
    [
        (date(2026, 1, 31), date(2026, 3, 17)),
        (date(2026, 12, 20), date(2027, 2, 3)),
        (date(2028, 2, 14), date(2028, 3, 30)),  # leap year: 29 Feb counts
    ],
)
def test_FR_001_01_decision_deadline_is_45_calendar_days_after_receipt(
    received_on: date, expected: date
) -> None:
    assert decision_deadline(received_on) == expected


@pytest.mark.parametrize(("from_state", "to_state"), list(product(CaseState, CaseState)))
def test_FR_001_02_only_table_transitions_are_allowed(
    from_state: CaseState, to_state: CaseState
) -> None:
    assert can_transition(from_state, to_state) == ((from_state, to_state) in ALLOWED_IN_001)


@pytest.mark.parametrize(
    ("from_state", "to_state"),
    [
        (S.verifying, S.awaiting_applicant),
        (S.awaiting_applicant, S.verifying),
        (S.awaiting_applicant, S.under_review),
        (S.verifying, S.under_review),
        (S.under_review, S.determining),
        (S.under_review, S.awaiting_applicant),
    ],
)
def test_FR_001_02_transitions_reserved_for_010_and_011_are_refused(
    from_state: CaseState, to_state: CaseState
) -> None:
    assert can_transition(from_state, to_state) is False


@pytest.mark.parametrize(
    ("received_on", "start", "end"),
    [
        (date(2026, 3, 17), date(2026, 3, 1), date(2027, 2, 28)),
        (date(2026, 1, 1), date(2026, 1, 1), date(2026, 12, 31)),
        (date(2026, 12, 31), date(2026, 12, 1), date(2027, 11, 30)),
        (date(2027, 3, 31), date(2027, 3, 1), date(2028, 2, 29)),  # leap end
    ],
)
def test_FR_001_22_coverage_starts_first_of_receipt_month_ends_last_day_of_12th_month(
    received_on: date, start: date, end: date
) -> None:
    assert coverage_for(received_on) == (start, end)


@given(DATES)
def test_FR_001_22_coverage_is_always_twelve_whole_months_property(received_on: date) -> None:
    start, end = coverage_for(received_on)

    day_after = end + timedelta(days=1)
    assert start.day == 1
    assert start <= received_on <= end
    assert day_after.day == 1
    months = (day_after.year - start.year) * 12 + day_after.month - start.month
    assert months == 12


@given(DATES)
def test_FR_001_18_deadline_minus_receipt_is_always_45_days_property(received_on: date) -> None:
    assert (decision_deadline(received_on) - received_on).days == 45


def test_FR_001_07_allowed_actions_for_001_review_reasons() -> None:
    both = [ActionType.approve_denial, ActionType.override_to_approve]

    assert allowed_actions_for(ReviewReason.adverse_determination) == both
    assert allowed_actions_for(ReviewReason.cannot_determine) == both
    for reason in (
        ReviewReason.notice_approval,
        ReviewReason.verification_review,
        ReviewReason.missed_response_deadline,
        ReviewReason.explanation_review,
    ):
        assert allowed_actions_for(reason) == []


def test_FR_001_16_denial_reason_precedence_income_first() -> None:
    adverse = ReviewReason.adverse_determination

    assert (
        denial_reason_for(
            adverse, [DenialReason.other_rule_not_met, DenialReason.income_over_limit]
        )
        is DenialReason.income_over_limit
    )
    assert denial_reason_for(adverse, ["other_rule_not_met"]) is DenialReason.other_rule_not_met
    assert denial_reason_for(adverse, []) is DenialReason.other_rule_not_met
    assert (
        denial_reason_for(ReviewReason.cannot_determine, [DenialReason.income_over_limit])
        is DenialReason.missing_information
    )
