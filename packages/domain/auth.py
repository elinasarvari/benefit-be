from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Role(StrEnum):
    applicant = "applicant"
    caseworker = "caseworker"
    supervisor = "supervisor"


class UserPublic(BaseModel):
    id: UUID
    username: str
    display_name: str
    role: Role
    household_id: UUID | None


class Principal(BaseModel):
    user_id: UUID
    role: Role
    household_id: UUID | None
    session_expires_at: datetime


class SystemCredentialClaims(BaseModel):
    """Claims of the case-scoped credential the worker issues for one agent step.

    There is deliberately no role field: the credential is not a user role (FR-000-22).
    """

    model_config = ConfigDict(extra="forbid")

    typ: Literal["system"] = "system"
    aud: Literal["agent-tools"] = "agent-tools"
    sub: Literal["system:agent"] = "system:agent"
    case_id: UUID
    workflow_id: str
    step: str
    iat: int
    exp: int
    jti: str


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=1, max_length=128)


class SessionInfo(BaseModel):
    user: UserPublic
    expires_at: datetime
