from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ErrorCode(StrEnum):
    auth_required = "auth.required"
    auth_session_expired = "auth.session_expired"
    auth_invalid_credentials = "auth.invalid_credentials"
    auth_forbidden = "auth.forbidden"
    not_found = "not_found"
    conflict = "conflict"
    validation_failed = "validation_failed"
    internal_error = "internal_error"
    dependency_unavailable = "dependency_unavailable"
    # A contract endpoint whose feature is not built yet (MVP contracts PR).
    not_implemented = "not_implemented"


class FieldError(BaseModel):
    field: str
    message: str
    type: str


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: ErrorCode
    message: str
    fields: list[FieldError]
    reference_id: str
    related_case_id: UUID | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody
