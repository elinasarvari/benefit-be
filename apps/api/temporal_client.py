"""Temporal client for the API: a connect timeout and the request ID on every call (R10)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from uuid import UUID

import structlog
from temporalio.api.common.v1 import Payload
from temporalio.client import (
    Client,
    Interceptor,
    OutboundInterceptor,
    QueryWorkflowInput,
    SignalWorkflowInput,
    StartWorkflowInput,
    WorkflowHandle,
    WorkflowQueryFailedError,
)
from temporalio.common import WorkflowIDReusePolicy
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.converter import PayloadConverter
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from packages.domain.case import AllowedActions, CaseStart, CaseworkerActionRequest, workflow_id_for
from packages.platform.errors import DependencyUnavailable, NotFound
from packages.platform.logging import REQUEST_ID_HEADER
from packages.platform.settings import Settings

CONNECT_TIMEOUT_S = 5.0

# Names from contracts/case-workflow.md (the API talks to the worker only through Temporal, BA-5).
CASE_WORKFLOW = "CaseWorkflow"
CASEWORKER_ACTION_SIGNAL = "caseworker_action"
ALLOWED_ACTIONS_QUERY = "allowed_actions"


def _with_request_id(headers: Mapping[str, Payload]) -> Mapping[str, Payload]:
    request_id = structlog.contextvars.get_contextvars().get("request_id")
    if not request_id:
        return headers
    payload = PayloadConverter.default.to_payloads([str(request_id)])[0]
    return {**headers, REQUEST_ID_HEADER: payload}


class _Outbound(OutboundInterceptor):
    async def start_workflow(self, input: StartWorkflowInput) -> WorkflowHandle:  # type: ignore[type-arg]
        input.headers = _with_request_id(input.headers)
        return await super().start_workflow(input)

    async def signal_workflow(self, input: SignalWorkflowInput) -> None:
        input.headers = _with_request_id(input.headers)
        await super().signal_workflow(input)

    async def query_workflow(self, input: QueryWorkflowInput) -> object:
        input.headers = _with_request_id(input.headers)
        return await super().query_workflow(input)


class RequestIdInterceptor(Interceptor):
    def intercept_client(self, next: OutboundInterceptor) -> OutboundInterceptor:  # noqa: A002
        return _Outbound(next)


class TemporalCaseWorkflows:
    """`CaseWorkflow` by name (contracts/case-workflow.md); the API never imports worker code.

    Temporal being unreachable is a 503 (FR-000-13); an unknown workflow is a 404.
    """

    def __init__(self, connect: Callable[[], Awaitable[Client]], task_queue: str) -> None:
        self._connect = connect
        self._task_queue = task_queue

    async def _client(self) -> Client:
        try:
            return await self._connect()
        except (TimeoutError, OSError, RuntimeError, RPCError) as error:
            raise _unavailable(error) from error

    async def _handle(self, case_id: UUID) -> WorkflowHandle:  # type: ignore[type-arg]
        return (await self._client()).get_workflow_handle(workflow_id_for(case_id))

    async def start(self, start: CaseStart) -> None:
        client = await self._client()
        try:
            await client.start_workflow(
                CASE_WORKFLOW,
                start,
                id=workflow_id_for(start.case_id),
                task_queue=self._task_queue,
                # A decided case's workflow is never started again by a retried submission.
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            )
        except WorkflowAlreadyStartedError:
            return  # idempotent start (ADR-001-02)
        except RPCError as error:
            raise _unavailable(error) from error

    async def allowed_actions(self, case_id: UUID) -> AllowedActions:
        handle = await self._handle(case_id)
        try:
            result = await handle.query(ALLOWED_ACTIONS_QUERY, result_type=AllowedActions)
            return AllowedActions.model_validate(result)
        except RPCError as error:
            if error.status is RPCStatusCode.NOT_FOUND:
                raise NotFound() from error
            raise _unavailable(error) from error
        except WorkflowQueryFailedError as error:
            raise _unavailable(error) from error

    async def signal_action(
        self, case_id: UUID, request: CaseworkerActionRequest, *, best_effort: bool = False
    ) -> bool:
        """True when delivered. With `best_effort`, a failed delivery (a completed workflow,
        say) returns False so the caller can record the refusal itself (ADR-001-04)."""
        handle = await self._handle(case_id)
        try:
            await handle.signal(CASEWORKER_ACTION_SIGNAL, request)
            return True
        except RPCError as error:
            if best_effort:
                structlog.get_logger().warning(
                    "case.action_signal_failed", status=error.status.name
                )
                return False
            if error.status is RPCStatusCode.NOT_FOUND:
                raise NotFound() from error
            raise _unavailable(error) from error


def _unavailable(error: Exception) -> DependencyUnavailable:
    structlog.get_logger().error(
        "dependency.unavailable", dependency="temporal", error=type(error).__name__
    )
    return DependencyUnavailable()


async def connect_temporal(settings: Settings) -> Client:
    return await asyncio.wait_for(
        Client.connect(
            settings.TEMPORAL_ADDRESS,
            namespace=settings.TEMPORAL_NAMESPACE,
            data_converter=pydantic_data_converter,
            interceptors=[RequestIdInterceptor()],
        ),
        timeout=CONNECT_TIMEOUT_S,
    )
