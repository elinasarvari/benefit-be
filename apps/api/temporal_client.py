"""Temporal client for the API: a connect timeout and the request ID on every call (R10)."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

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
)
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.converter import PayloadConverter

from packages.platform.logging import REQUEST_ID_HEADER
from packages.platform.settings import Settings

CONNECT_TIMEOUT_S = 5.0


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
