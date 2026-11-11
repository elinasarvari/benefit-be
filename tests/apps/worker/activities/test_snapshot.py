"""Snapshot capture inside the determination activity (T022, T041; TC-89–TC-94).

Fakes stand in for the stores, so these run without a database. Names are synthetic (PR-3).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import date

from apps.worker.activities.snapshot import snapshot_id_for
from packages.domain.case import ApplicationForm, DeterminationOutcome, Relationship
from packages.rules.engine import evaluate
from tests.apps.api.forms import form_dict
from tests.apps.worker.eligibility_fakes import (
    AS_OF,
    CASE,
    HOUSEHOLD,
    SELF_ID,
    SELF_JOB,
    SPOUSE_ID,
    SPOUSE_JOB,
    FakeSource,
    FakeVerified,
    capture,
    scope,
    stored_member,
    verified,
)


async def test_FR_003_19_existing_snapshot_for_case_and_date_is_reused() -> None:
    source = FakeSource(members=[stored_member()])
    first = await capture(source, FakeVerified([verified()])).capture(scope(), AS_OF)

    # The household changes after capture: a new member, and the applicant moves out of state.
    source.members = [
        stored_member(state="WI"),
        stored_member(SPOUSE_ID, relationship=Relationship.spouse),
    ]
    again = await capture(source, FakeVerified()).capture(scope(), AS_OF)

    assert again == first
    assert len(again.members) == 1
    assert again.members[0].address_state == "IL"
    assert source.insert_attempts == 1  # never recaptured
    assert len(source.snapshots) == 1


async def test_FR_003_19_snapshot_uses_members_of_the_as_of_year() -> None:
    source = FakeSource(
        members=[
            stored_member(year=2025),
            stored_member(year=2025, member_id=SPOUSE_ID, relationship=Relationship.spouse),
            stored_member(year=2026),
            stored_member(year=2026, member_id=SPOUSE_ID, relationship=Relationship.spouse),
            stored_member(
                year=2026, member_id=uuid.uuid5(CASE, "child"), relationship=Relationship.child
            ),
        ]
    )
    case_2025 = uuid.uuid5(CASE, "2025")

    in_2025 = await capture(source, FakeVerified()).capture(
        replace(scope(), case_id=case_2025), date(2025, 12, 31)
    )
    in_2026 = await capture(source, FakeVerified()).capture(scope(), date(2026, 1, 1))

    assert len(in_2025.members) == 2
    assert len(in_2026.members) == 3
    assert in_2025.as_of_date == date(2025, 12, 31)
    assert in_2025.snapshot_id == snapshot_id_for(case_2025, date(2025, 12, 31))


async def test_FR_003_16_snapshot_from_application_form_maps_attested_facts_and_leaves_identity_unknown() -> (  # noqa: E501
    None
):
    body = form_dict()
    body["members"][1]["citizenship_status"] = "neither"
    source = FakeSource(form=ApplicationForm.model_validate(body))

    snapshot = await capture(source, FakeVerified()).capture(scope(), AS_OF)

    applicant, spouse = snapshot.members
    assert applicant.member_id == uuid.uuid5(CASE, "member:0")
    assert applicant.relationship is Relationship.self
    assert applicant.address_state == "IL"
    assert applicant.citizenship_attested is True  # us_citizen
    assert spouse.citizenship_attested is False  # neither
    assert applicant.medicare_entitled is False
    assert applicant.identity_verified is None  # 005 provides it
    assert applicant.declared_zero_income is False
    assert spouse.declared_zero_income is True
    assert [(j.job_id, j.stubs) for j in applicant.jobs] == [(uuid.uuid5(CASE, "job:0:0"), [])]
    assert spouse.jobs == []


async def test_FR_003_16_qualifying_immigrant_is_attested_and_household_without_members_uses_form() -> (  # noqa: E501
    None
):
    source = FakeSource(form=ApplicationForm.model_validate(form_dict()))

    snapshot = await capture(source, FakeVerified()).capture(scope(), AS_OF)

    assert snapshot.members[1].citizenship_attested is True  # qualifying_immigrant
    assert snapshot.household_id == HOUSEHOLD


async def test_FR_003_03_form_job_without_verified_stub_gives_cannot_determine_not_ineligible() -> (
    None
):
    source = FakeSource(form=ApplicationForm.model_validate(form_dict()))

    snapshot = await capture(source, FakeVerified()).capture(scope(), AS_OF)
    result = evaluate(snapshot, AS_OF)

    assert result.result is DeterminationOutcome.cannot_determine
    assert "pay_stub" in {item.item for item in result.missing_items}


async def test_FR_003_17_verified_facts_merged_into_member_jobs_and_identity() -> None:
    source = FakeSource(
        members=[
            stored_member(zero_income=False, job_id=SELF_JOB),
            stored_member(
                SPOUSE_ID, relationship=Relationship.spouse, zero_income=False, job_id=SPOUSE_JOB
            ),
        ]
    )
    facts = FakeVerified(
        [
            verified(SELF_ID, identity=True, job_id=SELF_JOB, weekly_gross="400.00"),
            verified(SPOUSE_ID, identity=False, job_id=SPOUSE_JOB, weekly_gross="250.50"),
        ]
    )

    snapshot = await capture(source, facts).capture(scope(), AS_OF)

    applicant, spouse = snapshot.members
    assert (applicant.identity_verified, spouse.identity_verified) == (True, False)
    assert [str(s.gross_pay) for s in applicant.jobs[0].stubs] == ["400.00"]
    assert [str(s.gross_pay) for s in spouse.jobs[0].stubs] == ["250.50"]
    assert facts.scopes == [scope()]  # read under the case's worker scope


async def test_FR_003_19_captured_snapshot_contains_no_member_names() -> None:
    source = FakeSource(
        members=[stored_member(), stored_member(SPOUSE_ID, relationship=Relationship.spouse)]
    )

    snapshot = await capture(source, FakeVerified([verified()])).capture(scope(), AS_OF)

    text = json.dumps(snapshot.model_dump(mode="json"))
    assert "SYNTH-GIVEN" not in text
    assert "SYNTH-FAMILY" not in text
    assert "given_name" not in text and "family_name" not in text
