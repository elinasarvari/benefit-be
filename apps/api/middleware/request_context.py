from __future__ import annotations

import time
import uuid

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from apps.api.errors import error_response
from packages.domain.errors import ErrorCode
from packages.platform.logging import bind_context, clear_context

REQUEST_ID_HEADER = "X-Request-ID"
log = structlog.get_logger()


def _valid_request_id(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


class RequestContextMiddleware:
    """Gives every request a reference ID, binds it to the logs, and turns crashes into 500s.

    Bodies and query strings are never logged. The route template is logged instead of the path.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _valid_request_id(Headers(scope=scope).get(REQUEST_ID_HEADER)) or str(
            uuid.uuid4()
        )
        scope.setdefault("state", {})["request_id"] = request_id
        clear_context()
        bind_context(request_id=request_id)
        started = time.perf_counter()
        status_code = 500
        response_started = False

        async def send_with_header(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        except Exception:
            log.exception("error.unhandled", error_code=ErrorCode.internal_error.value)
            if response_started:
                raise
            status_code = 500
            response = error_response(ErrorCode.internal_error, request_id)
            await response(scope, receive, send)
        finally:
            route = scope.get("route")
            log.info(
                "request.completed",
                method=scope["method"],
                route=getattr(route, "path", "unmatched"),
                status=status_code,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            clear_context()
