from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel


class CheckStatus(StrEnum):
    ok = "ok"
    down = "down"
    degraded = "degraded"


class ReadinessStatus(StrEnum):
    ready = "ready"
    degraded = "degraded"
    not_ready = "not_ready"


class HealthReport(BaseModel):
    component: str
    status: Literal["ok"] = "ok"
    version: str
    synthetic_data: Literal[True] = True


class ReadinessCheck(BaseModel):
    name: str
    status: CheckStatus
    required: bool


class ReadinessReport(BaseModel):
    component: str
    status: ReadinessStatus
    checks: list[ReadinessCheck]

    @classmethod
    def from_checks(cls, component: str, checks: list[ReadinessCheck]) -> ReadinessReport:
        if any(c.required and c.status is CheckStatus.down for c in checks):
            status = ReadinessStatus.not_ready
        elif any(c.status is not CheckStatus.ok for c in checks):
            status = ReadinessStatus.degraded
        else:
            status = ReadinessStatus.ready
        return cls(component=component, status=status, checks=checks)
