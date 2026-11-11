"""Renewal check (FR-007-13) and the pre-filled request's items (FR-007-03). Pure functions.

Held data is compared with the reference lookup per member job: income more than 10% apart,
a reported job the member did not declare, a declared job with no reported income, or an
identity that no longer matches. Money is `Decimal`, compared at cents (research R2).
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID, uuid5

from packages.domain.case import DeterminationOutcome
from packages.domain.household import HouseholdMember, Job
from packages.domain.notice import RequestedItem, RequestedItemKind
from packages.domain.renewal import RenewalItem, RenewalItemKind
from packages.domain.verification import IdentityMatch, ReferenceAnswer
from packages.rules.income import _MONTHS, _PERIODS_PER_YEAR, CENT

INCOME_TOLERANCE = Decimal("0.10")

_KIND_FOR_ITEM = {
    RenewalItemKind.income_differs: RequestedItemKind.confirm_income,
    RenewalItemKind.job_missing_in_lookup: RequestedItemKind.confirm_job,
    RenewalItemKind.undeclared_job: RequestedItemKind.confirm_job,
    RenewalItemKind.identity_mismatch: RequestedItemKind.confirm_member,
}


def held_monthly(job: Job) -> Decimal:
    exact = job.declared_gross * _PERIODS_PER_YEAR[job.declared_frequency] / _MONTHS
    return exact.quantize(CENT)


def _differs(held: Decimal, reference: Decimal) -> bool:
    return abs(reference - held) > held * INCOME_TOLERANCE


def renewal_items(
    held: Sequence[HouseholdMember], answers: Sequence[ReferenceAnswer]
) -> list[RenewalItem]:
    by_member = {answer.member_id: answer for answer in answers}
    items: list[RenewalItem] = []
    for member in held:
        answer = by_member.get(member.member_id)
        if answer is None or answer.identity is not IdentityMatch.match:
            items.append(
                RenewalItem(kind=RenewalItemKind.identity_mismatch, member_id=member.member_id)
            )
            continue
        unused = dict(enumerate(answer.jobs))
        for job in member.jobs:
            employer = job.employer.casefold()
            index = next(
                (i for i, ref in unused.items() if ref.employer.casefold() == employer), None
            )
            if index is None:
                items.append(
                    RenewalItem(
                        kind=RenewalItemKind.job_missing_in_lookup,
                        member_id=member.member_id,
                        job_id=job.job_id,
                    )
                )
                continue
            reference = unused.pop(index)
            if _differs(held_monthly(job), reference.monthly_gross.quantize(CENT)):
                items.append(
                    RenewalItem(
                        kind=RenewalItemKind.income_differs,
                        member_id=member.member_id,
                        job_id=job.job_id,
                    )
                )
        items.extend(
            RenewalItem(
                kind=RenewalItemKind.undeclared_job,
                member_id=member.member_id,
                reference_job_index=index,
            )
            for index in unused
        )
    return items


def _label(
    item: RenewalItem, employers: dict[UUID, str], answers: dict[UUID, ReferenceAnswer]
) -> str:
    if item.kind is RenewalItemKind.identity_mismatch:
        return "Confirm the household member's identity details"
    if item.kind is RenewalItemKind.undeclared_job and item.reference_job_index is not None:
        reported = answers[item.member_id].jobs[item.reference_job_index].employer
        return f"Tell us about the job with {reported}"[:200]
    employer = employers.get(item.job_id) if item.job_id else None
    if item.kind is RenewalItemKind.job_missing_in_lookup:
        return f"Confirm you still work for {employer}"[:200]
    return f"Confirm your monthly income from {employer}"[:200]


def requested_items(
    notice_id: UUID,
    items: Sequence[RenewalItem],
    held: Sequence[HouseholdMember],
    answers: Sequence[ReferenceAnswer],
    determination: DeterminationOutcome,
) -> list[RequestedItem]:
    employers = {job.job_id: job.employer for member in held for job in member.jobs}
    by_member = {answer.member_id: answer for answer in answers}
    use = list(items)
    if not use and determination is not DeterminationOutcome.eligible:
        use = [
            RenewalItem(
                kind=RenewalItemKind.income_differs, member_id=member.member_id, job_id=job.job_id
            )
            for member in held
            for job in member.jobs
        ]
    return [
        RequestedItem(
            item_id=uuid5(
                notice_id,
                f"{item.kind}:{item.member_id}:{item.job_id or item.reference_job_index}",
            ),
            kind=_KIND_FOR_ITEM[item.kind],
            member_id=item.member_id,
            job_id=item.job_id,
            label=_label(item, employers, by_member),
        )
        for item in use
    ]
