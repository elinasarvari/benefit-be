"""Carry the request reference ID from a workflow's start to its activities (R10).

Imported inside the workflow sandbox, so it only imports `temporalio`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from temporalio.api.common.v1 import Payload
from temporalio.worker import (
    ActivityInboundInterceptor,  # noqa: F401
    ExecuteWorkflowInput,
    Interceptor,
    StartActivityInput,
    WorkflowInboundInterceptor,
    WorkflowInterceptorClassInput,
    WorkflowOutboundInterceptor,
)

REQUEST_ID_HEADER = "x-request-id"


class _Inbound(WorkflowInboundInterceptor):
    def __init__(self, next: WorkflowInboundInterceptor) -> None:  # noqa: A002
        super().__init__(next)
        self.request_id: Payload | None = None

    def init(self, outbound: WorkflowOutboundInterceptor) -> None:
        super().init(_Outbound(outbound, self))

    async def execute_workflow(self, input: ExecuteWorkflowInput) -> Any:
        self.request_id = input.headers.get(REQUEST_ID_HEADER)
        return await super().execute_workflow(input)


class _Outbound(WorkflowOutboundInterceptor):
    def __init__(self, next: WorkflowOutboundInterceptor, inbound: _Inbound) -> None:  # noqa: A002
        super().__init__(next)
        self._inbound = inbound

    def start_activity(self, input: StartActivityInput) -> Any:
        if self._inbound.request_id is not None:
            headers: Mapping[str, Payload] = {
                **input.headers,
                REQUEST_ID_HEADER: self._inbound.request_id,
            }
            input.headers = headers
        return super().start_activity(input)


class WorkflowRequestIdInterceptor(Interceptor):
    def workflow_interceptor_class(
        self,
        input: WorkflowInterceptorClassInput,  # noqa: A002
    ) -> type[WorkflowInboundInterceptor] | None:
        return _Inbound
