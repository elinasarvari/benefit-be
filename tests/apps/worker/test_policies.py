from __future__ import annotations

import ast
import uuid
from pathlib import Path

import pytest
from temporalio import activity
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ActivityError, ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from apps.worker.workflows.smoke import SmokeWorkflow
from packages.domain.smoke import SmokeInput

WORKER = Path(__file__).parents[3] / "apps" / "worker"
CALLS = {"execute_activity", "execute_local_activity", "start_activity", "start_local_activity"}
PRESETS = {"DEFAULT", "AGENT_STEP", "RULES_STEP", "UNTIL_DONE"}


def uses_preset(call: ast.Call) -> bool:
    return any(
        kw.arg is None
        and isinstance(kw.value, ast.Name | ast.Attribute)
        and ast.unparse(kw.value).split(".")[-1] in PRESETS
        for kw in call.keywords
    )


def test_FR_000_13_every_activity_call_uses_policy_preset() -> None:
    offenders = []
    for path in WORKER.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in CALLS
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "workflow"
                and not uses_preset(node)
            ):
                offenders.append(f"{path.relative_to(WORKER)}:{node.lineno}")

    assert offenders == [], f"activity calls without a policies.py preset: {offenders}"


def test_FR_010_03_rules_step_allows_3_attempts() -> None:
    from apps.worker.policies import NON_RETRYABLE_ERRORS, RULES_STEP

    assert RULES_STEP["retry_policy"].maximum_attempts == 3
    assert RULES_STEP["retry_policy"].non_retryable_error_types == NON_RETRYABLE_ERRORS
    assert RULES_STEP["start_to_close_timeout"].total_seconds() > 0


@pytest.mark.workflow
async def test_FR_000_13_validation_error_not_retried() -> None:
    attempts = 0

    @activity.defn(name="smoke_step")
    async def rejects(data: SmokeInput) -> str:
        nonlocal attempts
        attempts += 1
        raise ApplicationError("bad input", type="ValidationError")

    case_id = uuid.uuid4()
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        async with Worker(
            env.client, task_queue="test-policies", workflows=[SmokeWorkflow], activities=[rejects]
        ):
            with pytest.raises(Exception) as failure:  # noqa: PT011
                await env.client.execute_workflow(
                    SmokeWorkflow.run,
                    SmokeInput(case_id=case_id, delay_seconds=1),
                    id=f"case-{case_id}",
                    task_queue="test-policies",
                )

    assert attempts == 1
    assert isinstance(failure.value.__cause__, ActivityError)
