"""Inputs of the review activities that are not already `packages/domain` models.

They pass only between `apps/worker/workflows/review.py` and
`apps/worker/activities/review_tasks.py`, inside the Workflows lane, so they live here. No DB or
Temporal imports, so workflows may load them.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict

from packages.domain.review import ReviewFlag


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReviewFlagUpdate(_Payload):
    household_id: UUID
    case_id: UUID
    task_id: UUID
    flag: ReviewFlag
    at_case: AwareDatetime


class ReviewTaskClose(_Payload):
    household_id: UUID
    case_id: UUID
    task_id: UUID
    action_id: UUID
