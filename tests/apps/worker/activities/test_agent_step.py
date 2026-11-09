from __future__ import annotations

import io
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from temporalio.testing import ActivityEnvironment

from apps.worker.activities.agent_step import AgentStepActivities, ToolCall
from packages.domain.agent_step import AgentStepInput
from packages.platform.auth.tokens import verify_system_credential
from packages.platform.clock import FixedClock
from packages.platform.errors import CredentialRejected
from packages.platform.logging import clear_context, configure_logging
from packages.platform.settings import Settings

T0 = datetime(2026, 5, 4, 15, 0, tzinfo=UTC)
STEP_TIMEOUT = timedelta(seconds=120)


class RecordingTool:
    def __init__(self) -> None:
        self.credentials: list[str] = []

    async def __call__(self, case_id: uuid.UUID, *, tool_credential: str) -> str:
        self.credentials.append(tool_credential)
        return f"checked {case_id}"


def environment(attempt: int = 1) -> ActivityEnvironment:
    env = ActivityEnvironment()
    env.info = replace(
        env.info,
        start_to_close_timeout=STEP_TIMEOUT,
        workflow_id="case-123",
        attempt=attempt,
    )
    return env


def build(settings: Settings, tool: ToolCall) -> AgentStepActivities:
    return AgentStepActivities(settings, FixedClock(T0), tool)


async def test_FR_000_22_credential_expires_at_step_end(settings: Settings) -> None:
    tool = RecordingTool()
    case_id = uuid.uuid4()

    await environment().run(
        build(settings, tool).run, AgentStepInput(case_id=case_id, step="verify")
    )

    claims = verify_system_credential(tool.credentials[0], case_id, settings, FixedClock(T0))
    assert claims.exp - claims.iat == int(STEP_TIMEOUT.total_seconds())
    assert claims.workflow_id == "case-123"
    assert claims.step == "verify"
    with pytest.raises(CredentialRejected):
        verify_system_credential(
            tool.credentials[0], case_id, settings, FixedClock(T0 + STEP_TIMEOUT)
        )


async def test_FR_000_22_new_credential_per_retry_attempt(settings: Settings) -> None:
    tool = RecordingTool()
    activities = build(settings, tool)
    data = AgentStepInput(case_id=uuid.uuid4(), step="verify")

    await environment(attempt=1).run(activities.run, data)
    await environment(attempt=2).run(activities.run, data)

    first, second = (
        verify_system_credential(token, data.case_id, settings, FixedClock(T0))
        for token in tool.credentials
    )
    assert first.jti != second.jti


async def test_FR_000_22_token_not_in_activity_result_or_logs(settings: Settings) -> None:
    stream = io.StringIO()
    configure_logging(settings, "worker", stream=stream)
    tool = RecordingTool()

    try:
        result = await environment().run(
            build(settings, tool).run, AgentStepInput(case_id=uuid.uuid4(), step="verify")
        )
    finally:
        clear_context()

    token = tool.credentials[0]
    assert token not in result.model_dump_json()
    assert token not in stream.getvalue()


async def test_FR_000_22_token_never_reaches_a_log_call_even_before_masking(
    settings: Settings,
) -> None:
    from structlog.testing import capture_logs

    tool = RecordingTool()

    with capture_logs() as events:
        await environment().run(
            build(settings, tool).run, AgentStepInput(case_id=uuid.uuid4(), step="verify")
        )

    assert events, "the step must log something for this check to mean anything"
    assert tool.credentials[0] not in repr(events)
