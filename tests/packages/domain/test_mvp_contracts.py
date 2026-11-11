"""MVP contracts PR (specs/mvp-contracts.md): notices, verification, waits, redeterminations."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from packages.domain.case import (
    CaseChange,
    CaseClock,
    CaseEvent,
    CaseEventKind,
    CaseStart,
    CaseState,
    CaseStatus,
    CaseType,
    CoveragePeriod,
    CoverageStatus,
    CoverageStatusChange,
    EventActorType,
    NoticeType,
    new_redetermination_case,
    redetermination_case_id,
)
from packages.domain.notice import NOTICE_NEEDS_APPROVAL
from packages.domain.verification import Correction, VerificationResult

NOW = datetime(2026, 3, 17, 17, 0, tzinfo=UTC)


def _start(case_type: CaseType, coverage_period_id: object = None) -> CaseStart:
    return CaseStart.model_validate(
        {
            "case_id": uuid4(),
            "household_id": uuid4(),
            "case_type": case_type,
            "submission_reference": "ref",
            "received_at": NOW,
            "clock": CaseClock(real_start=NOW, case_start=NOW, scale=1),
            "coverage_period_id": coverage_period_id,
        }
    )


def test_FR_011_06_every_notice_type_says_whether_it_needs_approval() -> None:
    assert set(NOTICE_NEEDS_APPROVAL) == set(NoticeType)
    favourable = {
        NoticeType.approval,
        NoticeType.automatic_renewal,
        NoticeType.renewal_after_response,
    }
    assert {t for t, needs in NOTICE_NEEDS_APPROVAL.items() if not needs} == favourable


def test_FR_007_07_redetermination_case_id_is_one_per_coverage_period() -> None:
    household, period = uuid4(), uuid4()
    first = redetermination_case_id(household, period)
    assert redetermination_case_id(household, period) == first
    assert redetermination_case_id(household, uuid4()) != first
    assert redetermination_case_id(uuid4(), period) != first


def test_FR_007_05_coverage_period_required_only_for_redeterminations() -> None:
    assert _start(CaseType.application).coverage_period_id is None
    assert _start(CaseType.redetermination, uuid4()).coverage_period_id is not None
    with pytest.raises(ValidationError):
        _start(CaseType.redetermination)
    with pytest.raises(ValidationError):
        _start(CaseType.application, uuid4())


def test_FR_002_23_verification_result_decodes_001_outcome_payloads() -> None:
    """Histories recorded before the contract held only these two fields (TW-14)."""
    result = VerificationResult.model_validate({"complete": True, "needs_review": False})
    assert result.findings == [] and result.missing_items == []
    assert result.identity_verified is None


def test_FR_002_19_correction_only_names_an_entered_detail() -> None:
    fields = {
        "old_value": "1",
        "new_value": "2",
        "reviewer_id": uuid4(),
        "reason": "matches the document",
        "corrected_at": NOW,
    }
    assert Correction(field="gross_pay", **fields).field == "gross_pay"
    with pytest.raises(ValidationError):
        Correction(field="identity_verified", **fields)


def test_FR_011_05_case_status_has_no_pending_timer_by_default() -> None:
    status = CaseStatus(
        case_id=uuid4(),
        state=CaseState.verifying,
        received_on=date(2026, 3, 1),
        decision_deadline=date(2026, 4, 15),
    )
    assert status.pending_timer is None


def _change(household_id: object, prior_household_id: object) -> CaseChange:
    case_id = uuid4()
    event = CaseEvent.model_validate(
        {
            "_id": uuid4(),
            "case_id": case_id,
            "household_id": household_id,
            "seq": 1,
            "kind": CaseEventKind.coverage_continued,
            "case_time": NOW,
            "real_time": NOW,
            "actor_type": EventActorType.system,
            "reason_code": "coverage_end_passed",
            "created_at": NOW,
        }
    )
    return CaseChange(
        case_id=case_id,
        household_id=household_id,
        seq=1,
        event=event,
        prior_coverage=CoverageStatusChange(
            coverage_period_id=uuid4(),
            household_id=prior_household_id,
            status=CoverageStatus.continued_pending_review,
        ),
    )


def test_FR_007_05_prior_coverage_must_match_change_household() -> None:
    household = uuid4()
    assert _change(household, household).prior_coverage is not None
    with pytest.raises(ValidationError):
        _change(household, uuid4())


def test_FR_007_07_redetermination_case_id_is_stable_per_period() -> None:
    household = uuid4()
    period = CoveragePeriod.model_validate(
        {
            "_id": uuid4(),
            "household_id": household,
            "case_id": uuid4(),
            "start_date": date(2025, 4, 1),
            "end_date": date(2026, 3, 31),
            "status": CoverageStatus.active,
        }
    )
    first = new_redetermination_case(household, period, NOW, 1)
    again = new_redetermination_case(household, period, NOW, 1)
    assert (
        first.id == first.case_id == again.case_id == redetermination_case_id(household, period.id)
    )
    assert first.case_type is CaseType.redetermination
    assert first.coverage_period_id == period.id
    assert first.decision_deadline == period.end_date
