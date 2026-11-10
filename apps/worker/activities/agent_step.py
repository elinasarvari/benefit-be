"""One agent step, with a case-scoped credential for its tool calls (FR-000-22, R6).

The credential is minted here, inside the activity, because signing needs a secret and a clock,
which workflow code may not use (TW-1). A fresh one is minted on every attempt, so retries are safe
(TW-11). It expires when the step's start-to-close timeout does, is passed straight to the call, and
is never returned, logged, or written to Temporal history.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

import structlog
from temporalio import activity
from temporalio.exceptions import ApplicationError

from packages.domain.agent_step import AgentStepInput, AgentStepResult
from packages.platform.auth.tokens import issue_system_credential, verify_system_credential
from packages.platform.clock import Clock
from packages.platform.settings import Settings

log = structlog.get_logger()

ToolCall = Callable[..., Awaitable[str]]


def smoke_tool(settings: Settings, clock: Clock) -> ToolCall:
    """A fake tool that checks the credential the way an MCP server would."""

    async def call(case_id: UUID, *, tool_credential: str) -> str:
        verify_system_credential(tool_credential, case_id, settings, clock)
        return f"tool accepted the credential for case {case_id}"

    return call


class AgentStepActivities:
    """Built once per worker process (TW-12), with its dependencies injected."""

    def __init__(self, settings: Settings, clock: Clock, tool: ToolCall) -> None:
        self._settings, self._clock, self._tool = settings, clock, tool

    @activity.defn(name="agent_step")
    async def run(self, data: AgentStepInput) -> AgentStepResult:
        info = activity.info()
        ttl = info.start_to_close_timeout
        if ttl is None:
            raise ApplicationError(
                "an agent step needs a start-to-close timeout (TW-10)", non_retryable=True
            )
        if not info.workflow_id:
            raise ApplicationError("an agent step must run inside a workflow", non_retryable=True)
        credential = issue_system_credential(
            data.case_id, info.workflow_id, data.step, ttl, self._settings, self._clock
        )
        summary = await self._tool(data.case_id, tool_credential=credential)
        log.info("agent_step.completed", step=data.step, attempt=info.attempt)
        return AgentStepResult(summary=summary)
