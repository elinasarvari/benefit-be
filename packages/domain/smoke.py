from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel


class SmokeInput(BaseModel):
    """Input of the platform smoke workflow, which exists only to prove the worker plumbing."""

    case_id: UUID
    delay_seconds: int = 86400  # one day; tests use a short delay against a real server


class SmokeResult(BaseModel):
    started_on: date
    activity_result: str
