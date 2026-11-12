"""Acceptance rules for a caseworker's correction or dismissal (FR-002-19; ADR-002-06): pure.

The activity loads the stored details and result, calls one of these and stores what it returns
(TW-9). A refusal is `ActionRefused` carrying a code, never the caller's text (PL-11). Both
functions are idempotent on `action_id`: a replayed action changes nothing (TW-11).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from uuid import UUID

from pydantic import ValidationError

from packages.domain.verification import (
    Correction,
    Dismissal,
    PayStubDetails,
    VerificationResult,
    effective,
)

CORRECTABLE_FIELDS = frozenset({"employee_name", "employer", "frequency", "gross_pay", "pay_date"})


class ActionRefused(Exception):  # noqa: N818 - a refusal code, not an error condition
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def parse_field_ref(field_ref: str) -> tuple[UUID, str]:
    parts = field_ref.split(":")
    if len(parts) != 3 or parts[0] != "pay_stub" or parts[2] not in CORRECTABLE_FIELDS:
        raise ActionRefused("unknown_field")
    try:
        return UUID(parts[1]), parts[2]
    except ValueError:
        raise ActionRefused("unknown_field") from None


def accept_correction(
    stored: Sequence[PayStubDetails],
    *,
    action_id: UUID,
    field_ref: str,
    value: object,
    reviewer_id: UUID,
    reason: str,
    submitted_at: datetime,
) -> tuple[UUID, Correction] | None:
    """The correction to append and the document it belongs to, or None for a replay."""
    document_id, field = parse_field_ref(field_ref)
    details = next((d for d in stored if d.document_id == document_id), None)
    if details is None:
        raise ActionRefused("unknown_field")
    if value is None:
        raise ActionRefused("invalid_value")
    if any(c.action_id == action_id for c in details.corrections):
        return None
    correction = Correction(
        field=field,
        old_value=str(getattr(effective(details), field)),
        new_value=str(value),
        reviewer_id=reviewer_id,
        reason=reason,
        corrected_at=submitted_at,
        action_id=action_id,
    )
    try:
        effective(details.model_copy(update={"corrections": [*details.corrections, correction]}))
    except ValidationError:
        raise ActionRefused("invalid_value") from None
    return details.document_id, correction


def accept_dismissal(
    stored: VerificationResult | None,
    dismissed: Mapping[UUID, Dismissal],
    *,
    finding_id: UUID,
    reviewer_id: UUID,
    reason: str,
    submitted_at: datetime,
) -> dict[UUID, Dismissal]:
    """The dismissals after this one; a finding already dismissed keeps its first dismissal."""
    known = {f.finding_id for f in stored.findings} if stored else set()
    if finding_id not in known:
        raise ActionRefused("unknown_finding")
    if finding_id in dismissed:
        return dict(dismissed)
    dismissal = Dismissal(reviewer_id=reviewer_id, reason=reason, dismissed_at=submitted_at)
    return {**dismissed, finding_id: dismissal}
