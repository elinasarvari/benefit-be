"""Renewal check and pre-filled request items (007, FR-007-13, FR-007-03)."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from packages.domain.case import DeterminationOutcome, PayFrequency, Relationship
from packages.domain.household import HouseholdMember, Job
from packages.domain.notice import RequestedItemKind
from packages.domain.renewal import RenewalItemKind
from packages.domain.verification import IdentityMatch, ReferenceAnswer, ReferenceJob
from packages.rules.renewal import renewal_items, requested_items

NS = uuid.uuid5(uuid.NAMESPACE_URL, "https://benefit-case-platform.example/renewal-tests")
HOUSEHOLD = uuid.uuid5(NS, "household")
MEMBER = uuid.uuid5(NS, "member")
JOB = uuid.uuid5(NS, "job")
JOB_2 = uuid.uuid5(NS, "job-2")
NOTICE = uuid.uuid5(NS, "notice")


def _job(job_id: uuid.UUID, employer: str, monthly: str) -> Job:
    return Job(
        job_id=job_id,
        employer=employer,
        declared_frequency=PayFrequency.monthly,
        declared_gross=Decimal(monthly),
    )


def _member(*jobs: Job) -> HouseholdMember:
    return HouseholdMember(
        member_id=MEMBER,
        household_id=HOUSEHOLD,
        given_name="Synthetic",
        family_name="Person",
        date_of_birth=date(1990, 1, 1),
        relationship=Relationship.self,
        address_state="TX",
        citizenship_attested=True,
        medicare_entitled=False,
        declared_zero_income=not jobs,
        jobs=list(jobs),
        coverage_year=2026,
    )


def _answer(
    *jobs: tuple[str, str], identity: IdentityMatch = IdentityMatch.match
) -> ReferenceAnswer:
    return ReferenceAnswer(
        member_id=MEMBER,
        identity=identity,
        jobs=[ReferenceJob(employer=e, monthly_gross=Decimal(m)) for e, m in jobs],
    )


def _kinds(items: list) -> list[RenewalItemKind]:
    return [item.kind for item in items]


def test_FR_007_13_income_differs_over_ten_percent_is_out_of_date() -> None:
    held = [_member(_job(JOB, "Acme", "1000"))]
    over = renewal_items(held, [_answer(("Acme", "1101"))])
    exact = renewal_items(held, [_answer(("Acme", "1100"))])
    assert _kinds(over) == [RenewalItemKind.income_differs]
    assert (over[0].member_id, over[0].job_id) == (MEMBER, JOB)
    assert exact == []


def test_FR_007_13_undeclared_job_in_lookup_is_out_of_date() -> None:
    held = [_member(_job(JOB, "Acme", "1000"))]
    items = renewal_items(held, [_answer(("acme", "1000"), ("Other Co", "500"))])
    assert _kinds(items) == [RenewalItemKind.undeclared_job]
    assert items[0].job_id is None
    assert items[0].reference_job_index == 1


def test_FR_007_13_declared_job_without_lookup_income_is_missing() -> None:
    held = [_member(_job(JOB, "Acme", "1000"))]
    items = renewal_items(held, [_answer()])
    assert _kinds(items) == [RenewalItemKind.job_missing_in_lookup]
    assert items[0].job_id == JOB


def test_FR_007_13_identity_mismatch_is_out_of_date() -> None:
    held = [_member(_job(JOB, "Acme", "1000"))]
    for identity in (IdentityMatch.no_match, IdentityMatch.no_record):
        items = renewal_items(held, [_answer(("Acme", "1000"), identity=identity)])
        assert _kinds(items) == [RenewalItemKind.identity_mismatch]
    assert _kinds(renewal_items(held, [])) == [RenewalItemKind.identity_mismatch]


def test_FR_007_13_matching_data_lists_no_items() -> None:
    held = [_member(_job(JOB, "Acme", "1000"), _job(JOB_2, "Beta", "2000"))]
    answers = [_answer(("Acme", "1050"), ("Beta", "1900"))]
    assert renewal_items(held, answers) == []
    assert renewal_items([_member()], [_answer()]) == []


def test_FR_007_03_requested_items_list_only_differing_job_income() -> None:
    held = [_member(_job(JOB, "Acme", "1000"), _job(JOB_2, "Beta", "2000"))]
    answers = [_answer(("Acme", "1200"), ("Beta", "2000"))]
    items = renewal_items(held, answers)
    requested = requested_items(NOTICE, items, held, answers, DeterminationOutcome.eligible)
    assert [(r.kind, r.member_id, r.job_id) for r in requested] == [
        (RequestedItemKind.confirm_income, MEMBER, JOB)
    ]
    assert (
        requested[0].item_id
        == requested_items(NOTICE, items, held, answers, DeterminationOutcome.eligible)[0].item_id
    )


def test_FR_007_03_ineligible_without_items_requests_each_declared_job_income() -> None:
    held = [_member(_job(JOB, "Acme", "1000"), _job(JOB_2, "Beta", "2000"))]
    answers = [_answer(("Acme", "1000"), ("Beta", "2000"))]
    assert renewal_items(held, answers) == []
    requested = requested_items(NOTICE, [], held, answers, DeterminationOutcome.ineligible)
    assert {(r.kind, r.job_id) for r in requested} == {
        (RequestedItemKind.confirm_income, JOB),
        (RequestedItemKind.confirm_income, JOB_2),
    }
    assert requested_items(NOTICE, [], held, answers, DeterminationOutcome.eligible) == []
