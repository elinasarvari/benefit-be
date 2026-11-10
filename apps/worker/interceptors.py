"""Bind workflow, case and request IDs to activity logs (TW-17, FR-000-10)."""

from __future__ import annotations

from typing import Any

from temporalio import activity
from temporalio.converter import PayloadConverter
from temporalio.worker import ActivityInboundInterceptor, ExecuteActivityInput, Interceptor

from apps.worker.workflow_interceptor import WorkflowRequestIdInterceptor
from packages.platform.logging import REQUEST_ID_HEADER, bind_context, clear_context

CASE_WORKFLOW_PREFIX = "case-"


def _case_id(input: ExecuteActivityInput, workflow_id: str) -> str | None:  # noqa: A002
    first = input.args[0] if input.args else None
    case_id = getattr(first, "case_id", None)
    if case_id is not None:
        return str(case_id)
    if workflow_id.startswith(CASE_WORKFLOW_PREFIX):
        return workflow_id.removeprefix(CASE_WORKFLOW_PREFIX)
    return None


class _ActivityInbound(ActivityInboundInterceptor):
    async def execute_activity(self, input: ExecuteActivityInput) -> Any:  # noqa: A002
        info = activity.info()
        header = input.headers.get(REQUEST_ID_HEADER)
        request_id = PayloadConverter.default.from_payloads([header], [str])[0] if header else None
        clear_context()
        bind_context(
            workflow_id=info.workflow_id,
            run_id=info.workflow_run_id,
            activity_type=info.activity_type,
            attempt=info.attempt,
            case_id=_case_id(input, info.workflow_id or ""),
            request_id=request_id,
        )
        try:
            return await super().execute_activity(input)
        finally:
            clear_context()


class ContextInterceptor(Interceptor):
    """Worker interceptor: log context for activities, and request ID propagation in workflows."""

    def __init__(self) -> None:
        self._workflow = WorkflowRequestIdInterceptor()

    def intercept_activity(self, next: ActivityInboundInterceptor) -> ActivityInboundInterceptor:  # noqa: A002
        return _ActivityInbound(next)

    def workflow_interceptor_class(self, input):  # type: ignore[no-untyped-def]  # noqa: A002
        return self._workflow.workflow_interceptor_class(input)
