from datetime import UTC, date, datetime
from uuid import UUID

from packages.domain.eligibility import EligibilityResult, RuleId, RuleOutcome
from packages.domain.household import HouseholdSnapshot, MemberFacts
from packages.rules.stub import StubRulesEngine


def _snapshot() -> HouseholdSnapshot:
    return HouseholdSnapshot(
        snapshot_id=UUID("00000000-0000-5000-8000-000000000005"),
        household_id=UUID("00000000-0000-5000-8000-000000000002"),
        case_id=UUID("00000000-0000-5000-8000-000000000006"),
        captured_at=datetime(2026, 11, 10, tzinfo=UTC),
        members=[
            MemberFacts(
                member_id=UUID("00000000-0000-5000-8000-000000000001"),
                relationship="self",
                date_of_birth=date(1990, 1, 1),
                address_state="IL",
                citizenship_attested=True,
                medicare_entitled=False,
                identity_verified=True,
                declared_zero_income=True,
                jobs=[],
            )
        ],
    )


def test_FR_003_05_stub_engine_returns_eligible_with_six_passing_rule_results() -> None:
    as_of = date(2026, 6, 1)

    determination = StubRulesEngine().evaluate(_snapshot(), as_of)

    assert determination.result is EligibilityResult.eligible
    assert determination.as_of_date == as_of
    assert [r.rule_id for r in determination.rule_results] == list(RuleId)
    assert all(r.outcome is RuleOutcome.passed for r in determination.rule_results)
    assert all(r.policy_ref for r in determination.rule_results)
    assert determination.missing_items == []
