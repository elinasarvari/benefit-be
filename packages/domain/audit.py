from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from packages.domain.auth import Role


class AuditAction(StrEnum):
    sign_in = "sign_in"
    access_refused = "access_refused"
    caseworker_action = "caseworker_action"
    demo_clock_action = "demo_clock_action"
    redetermination_started_on_demand = "redetermination_started_on_demand"
    redetermination_started_by_schedule = "redetermination_started_by_schedule"
    metrics_refreshed = "metrics_refreshed"
    determinations_read = "determinations_read"


class ActorType(StrEnum):
    user = "user"
    system = "system"
    anonymous = "anonymous"


class AuditOutcome(StrEnum):
    success = "success"
    failure = "failure"
    refused = "refused"


class ReasonCode(StrEnum):
    invalid_credentials = "invalid_credentials"
    role_not_allowed = "role_not_allowed"
    wrong_household = "wrong_household"
    wrong_case = "wrong_case"
    credential_expired = "credential_expired"


class AuditLogEntry(BaseModel):
    """One append-only audit record. It has no free-text field, so personal data has no place."""

    model_config = ConfigDict(extra="forbid")

    occurred_at: datetime
    actor_type: ActorType
    actor_id: str | None = Field(default=None, max_length=128)
    actor_role: Role | None = None
    action: AuditAction
    target_type: str | None = Field(default=None, max_length=128)
    target_id: str | None = Field(default=None, max_length=128)
    outcome: AuditOutcome
    reason_code: ReasonCode | None = None
    reference_id: str = Field(max_length=128)
