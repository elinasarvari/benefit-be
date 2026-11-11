from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

import packages.domain.eligibility as eligibility_module
import packages.domain.household as household_module
from packages.domain.case import DeterminationOutcome, PayFrequency, Relationship
from packages.domain.eligibility import (
    Determination,
    DeterminationRecord,
    DeterminationView,
    MissingItem,
    PovertyGuideline,
    RuleId,
    RuleOutcome,
    RuleResult,
    RuleSet,
)

MEMBER_ID = UUID("00000000-0000-5000-8000-000000000001")


def _rule_set() -> RuleSet:
    return RuleSet(
        rule_set_id="IL-ADULT-2026",
        version=1,
        program_year=2026,
        effective_from=date(2026, 1, 1),
        effective_to=date(2026, 12, 31),
        state="IL",
        age_min=19,
        age_max=64,
        fpl_percent=138,
        poverty_guideline=PovertyGuideline(
            base_annual=Decimal("15500"), per_additional_annual=Decimal("5200")
        ),
        policy_refs={rule: f"TM-{i}" for i, rule in enumerate(RuleId, start=1)},
    )


def _determination() -> Determination:
    missing = MissingItem(
        item="citizenship_attested", member_id=MEMBER_ID, job_id=None, reason="not answered"
    )
    return Determination(
        result=DeterminationOutcome.cannot_determine,
        as_of_date=date(2026, 6, 1),
        rule_set_id="IL-ADULT-2026",
        rule_set_version=1,
        engine_version="0.0.0",
        household_size=1,
        household_monthly_income=Decimal("1733.33"),
        monthly_income_limit=Decimal("1782"),
        rule_results=[
            RuleResult(
                rule_id=rule,
                outcome=RuleOutcome.missing if rule is RuleId.ELIG_03 else RuleOutcome.passed,
                inputs={"member_id": str(MEMBER_ID)},
                threshold=None,
                policy_ref="TM-2.1",
                missing=[missing] if rule is RuleId.ELIG_03 else [],
            )
            for rule in RuleId
        ],
        missing_items=[missing],
    )


def test_FR_003_02_eligibility_enums_and_determination_round_trip_json() -> None:
    assert [r.value for r in RuleId] == [f"ELIG-0{i}" for i in range(1, 7)]
    assert [o.value for o in RuleOutcome] == ["pass", "fail", "missing"]
    assert [e.value for e in DeterminationOutcome] == ["eligible", "ineligible", "cannot_determine"]

    determination = _determination()
    as_json = determination.model_dump(mode="json")

    assert Determination.model_validate_json(determination.model_dump_json()) == determination
    assert as_json["household_monthly_income"] == "1733.33"
    assert as_json["rule_results"][0]["outcome"] == "pass"


def test_FR_003_06_rule_set_round_trips_and_rejects_other_state() -> None:
    rule_set = _rule_set()

    assert RuleSet.model_validate_json(rule_set.model_dump_json()) == rule_set
    assert rule_set.model_dump(mode="json")["policy_refs"]["ELIG-01"] == "TM-1"
    with pytest.raises(ValidationError):
        RuleSet(**{**rule_set.model_dump(), "state": "WI"})


def test_FR_003_02_determination_allows_no_rule_set() -> None:
    determination = Determination(
        result=DeterminationOutcome.cannot_determine,
        as_of_date=date(2027, 1, 1),
        rule_set_id=None,
        rule_set_version=None,
        engine_version="0.0.0",
        household_size=1,
        household_monthly_income=None,
        monthly_income_limit=None,
        rule_results=[],
        missing_items=[MissingItem(item="rule_set", reason="no rules for date")],
    )

    assert determination.missing_items[0].member_id is None


def test_FR_003_19_determination_record_wraps_engine_output() -> None:
    record = DeterminationRecord(
        determination_id=UUID("00000000-0000-5000-8000-000000000010"),
        household_id=UUID("00000000-0000-5000-8000-000000000011"),
        case_id=UUID("00000000-0000-5000-8000-000000000012"),
        snapshot_id=UUID("00000000-0000-5000-8000-000000000013"),
        recorded_at=datetime(2026, 11, 10, tzinfo=UTC),
        determination=_determination(),
    )

    assert DeterminationRecord.model_validate_json(record.model_dump_json()) == record


def test_FR_003_02_eligibility_reuses_main_outcome_pay_frequency_and_relationship() -> None:
    assert not hasattr(eligibility_module, "EligibilityResult")
    assert household_module.PayFrequency is PayFrequency
    assert household_module.Relationship is Relationship
    assert len(Relationship) == 8


def test_FR_003_23_determination_view_has_contract_fields_and_forbids_extra() -> None:
    expected = {
        "determination_id", "result", "reason_code", "as_of_date", "rule_set_id",
        "rule_set_version", "household_size", "household_monthly_income",
        "monthly_income_limit", "rules", "missing_items", "recorded_at",
    }  # fmt: skip
    assert set(DeterminationView.model_fields) == expected

    determination = _determination()
    fields = {
        "determination_id": UUID("00000000-0000-5000-8000-000000000010"),
        "result": determination.result,
        "reason_code": None,
        "as_of_date": determination.as_of_date,
        "rule_set_id": determination.rule_set_id,
        "rule_set_version": determination.rule_set_version,
        "household_size": determination.household_size,
        "household_monthly_income": determination.household_monthly_income,
        "monthly_income_limit": determination.monthly_income_limit,
        "rules": determination.rule_results,
        "missing_items": determination.missing_items,
        "recorded_at": datetime(2026, 11, 10, tzinfo=UTC),
    }
    view = DeterminationView(**fields)
    assert DeterminationView.model_validate_json(view.model_dump_json()) == view
    with pytest.raises(ValidationError):
        DeterminationView(**fields, extra_field=1)
