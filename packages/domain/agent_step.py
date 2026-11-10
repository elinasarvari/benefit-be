from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class AgentStepInput(BaseModel):
    case_id: UUID
    step: str


class AgentStepResult(BaseModel):
    """What an agent step returns. It never carries the tool credential."""

    summary: str
