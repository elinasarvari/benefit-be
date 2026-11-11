"""The real `determine_eligibility` over fake stores (T023, T024, T042; TC-95–TC-98).

ADR-003-02: the activity stores the full record and returns 001's summary, unchanged in shape.
All households are synthetic (PR-3).
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date

import pytest
from structlog.testing import capture_logs
from temporalio.testing import ActivityEnvironment

from apps.worker.policies import WARN_AFTER_ATTEMPTS
from packages.domain.case import (
    DenialReason,
    DeterminationOutcome,
    DeterminationRequest,
)
from tests.apps.worker.eligibility_fakes import (
    AS_OF,
    CASE,
    HOUSEHOLD,
    SELF_ID,
    SELF_JOB,
    FakeSource,
    FakeStore,
    FakeVerified,
    activities,
    stored_member,
    verified,
)

REQUEST = DeterminationRequest(case_id=CASE, household_id=HOUSEHOLD, as_of_date=AS_OF)
Outcome = DeterminationOutcome
Reason = DenialReason


def household(
    *, state: str = "IL", weekly_gross: str | None = None, identity: bool | None = True
) -> tuple[FakeSource, FakeVerified]:
    """One applicant: no income, or one job with a verified weekly pay stub."""
    employed = weekly_gross is not None
    source = FakeSource(
        members=[
            stored_member(
                state=state, zero_income=not employed, job_id=SELF_JOB if employed else None
            )
        ]
    )
    facts = verified(
        SELF_ID, identity=identity, job_id=SELF_JOB if employed else None, weekly_gross=weekly_gross
    )
    return source, FakeVerified([facts])


async def test_FR_003_04_same_request_twice_stores_one_record_and_returns_identical_summary() -> (
    None
):
    source, facts = household()
    store = FakeStore()
    subject = activities(source, facts, store)
    env = ActivityEnvironment()

    first = await env.run(subject.determine_eligibility, REQUEST)
    again = await env.run(subject.determine_eligibility, REQUEST)

    assert again == first
    assert first.outcome is Outcome.eligible
    assert len(store.records) == 1
    assert len(source.snapshots) == 1
    [record] = store.records.values()
    assert record.snapshot_id == next(iter(source.snapshots.values())).snapshot_id
    assert len(record.determination.rule_results) == 6


async def test_FR_003_19_member_change_after_capture_does_not_change_determination() -> None:
    source, facts = household(weekly_gross="100.00")
    store = FakeStore()
    subject = activities(source, facts, store)
    env = ActivityEnvironment()

    first = await env.run(subject.determine_eligibility, REQUEST)
    # Income jumps over the limit after the snapshot was captured: same case and date.
    source.members = [stored_member(job_id=SELF_JOB, zero_income=False)]
    facts.facts = [verified(SELF_ID, job_id=SELF_JOB, weekly_gross="9000.00")]
    again = await env.run(subject.determine_eligibility, REQUEST)

    assert first.outcome is Outcome.eligible
    assert again == first
    assert len(store.records) == 1


@pytest.mark.parametrize(
    ("kwargs", "outcome", "reason", "failed", "missing"),
    [
        ({}, Outcome.eligible, None, [], []),
        (
            {"weekly_gross": "5000.00"},
            Outcome.ineligible,
            Reason.income_over_limit,
            [Reason.income_over_limit],
            [],
        ),
        (
            {"state": "WI"},
            Outcome.ineligible,
            Reason.other_rule_not_met,
            [Reason.other_rule_not_met],
            [],
        ),
        (
            {"state": "WI", "weekly_gross": "5000.00"},
            Outcome.ineligible,
            Reason.income_over_limit,
            [Reason.other_rule_not_met, Reason.income_over_limit],
            [],
        ),
        (
            {"identity": None},
            Outcome.cannot_determine,
            None,
            [],
            ["identity_verified"],
        ),
    ],
)
async def test_FR_003_02_summary_maps_each_outcome_to_reason_failed_rules_and_missing_items(
    kwargs: dict[str, object],
    outcome: DeterminationOutcome,
    reason: DenialReason | None,
    failed: list[DenialReason],
    missing: list[str],
) -> None:
    source, facts = household(**kwargs)  # type: ignore[arg-type]
    store = FakeStore()

    summary = await ActivityEnvironment().run(
        activities(source, facts, store).determine_eligibility, REQUEST
    )

    assert summary.outcome is outcome
    assert summary.reason_code == reason
    assert summary.failed_rules == failed
    assert summary.missing_items == missing
    assert summary.as_of_date == AS_OF
    [record] = store.records.values()
    assert record.determination.result is outcome  # the summary is the stored result


async def test_FR_003_02_determination_id_matches_stub_formula() -> None:
    """Research R6: `uuid5(case_id, "determination:<as_of>")`, so stored references survive."""
    source, facts = household()
    store = FakeStore()

    summary = await ActivityEnvironment().run(
        activities(source, facts, store).determine_eligibility, REQUEST
    )

    expected = uuid.uuid5(CASE, f"determination:{AS_OF.isoformat()}")
    assert summary.determination_id == expected
    assert list(store.records) == [expected]  # `_id` = determination_id (ADR-003-02)
    other_day = REQUEST.model_copy(update={"as_of_date": date(2026, 3, 18)})
    again = await ActivityEnvironment().run(
        activities(source, facts, store).determine_eligibility, other_day
    )
    assert again.determination_id == uuid.uuid5(CASE, "determination:2026-03-18")


async def test_FR_003_04_store_outage_raises_so_the_server_retries() -> None:
    source, facts = household()
    store = FakeStore(failures=1)
    subject = activities(source, facts, store)
    env = ActivityEnvironment()

    with pytest.raises(RuntimeError):
        await env.run(subject.determine_eligibility, REQUEST)
    summary = await env.run(subject.determine_eligibility, REQUEST)

    assert summary.outcome is Outcome.eligible
    assert len(store.records) == 1


@pytest.mark.parametrize("attempt", [1, WARN_AFTER_ATTEMPTS, WARN_AFTER_ATTEMPTS + 1, 40])
async def test_FR_001_03_outage_retry_logs_warning_with_attempt_only(attempt: int) -> None:
    """UNTIL_DONE retries are invisible in history, so later attempts warn in the log (BE-6)."""
    source, facts = household()
    env = ActivityEnvironment()
    env.info = replace(env.info, workflow_id=f"case-{CASE}", attempt=attempt)

    with capture_logs() as logs:
        await env.run(activities(source, facts, FakeStore()).determine_eligibility, REQUEST)

    warnings = [entry for entry in logs if entry["log_level"] == "warning"]
    if attempt <= WARN_AFTER_ATTEMPTS:
        assert warnings == []
        return
    assert warnings == [
        {
            "event": "activity.retrying",
            "log_level": "warning",
            "activity_type": env.info.activity_type,
            "attempt": attempt,
            "workflow_id": f"case-{CASE}",
        }
    ]  # IDs and the attempt number only, no personal data (PL-11)
