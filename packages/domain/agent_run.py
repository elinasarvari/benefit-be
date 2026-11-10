from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class AgentRunContext(BaseModel):
    case_id: UUID
    workflow_id: str


class AgentRunUsage(BaseModel):
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    duration_ms: int
    recorded: bool
