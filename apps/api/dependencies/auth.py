from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

import structlog
from fastapi import Depends, Request

from apps.api.dependencies.db import get_audit_writer, get_clock, get_settings
from packages.domain.audit import (
    ActorType,
    AuditAction,
    AuditLogEntry,
    AuditOutcome,
    ReasonCode,
)
from packages.domain.auth import Principal, Role
from packages.platform.audit import AuditWriter
from packages.platform.auth.roles import has_role
from packages.platform.auth.tokens import verify_user_token
from packages.platform.clock import Clock
from packages.platform.errors import AuthRequired, Forbidden
from packages.platform.logging import bind_context
from packages.platform.settings import Settings

SESSION_COOKIE = "benefit_session"


async def current_principal(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> Principal:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise AuthRequired()
    principal = verify_user_token(token, settings, clock)
    bind_context(user_id=str(principal.user_id), role=principal.role.value)
    return principal


def route_template(request: Request) -> str:
    route = request.scope.get("route")
    return str(getattr(route, "path", request.url.path))


def request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "unknown"))


def require_roles(*allowed: Role) -> Callable[..., Awaitable[Principal]]:
    """Allow the listed roles (a supervisor counts as a caseworker). Refusals are audited."""

    async def dependency(
        request: Request,
        principal: Annotated[Principal, Depends(current_principal)],
        audit: Annotated[AuditWriter, Depends(get_audit_writer)],
        clock: Annotated[Clock, Depends(get_clock)],
    ) -> Principal:
        if has_role(principal, *allowed):
            return principal
        await audit.write(
            AuditLogEntry(
                occurred_at=clock.now(),
                actor_type=ActorType.user,
                actor_id=str(principal.user_id),
                actor_role=principal.role,
                action=AuditAction.access_refused,
                target_type="route",
                target_id=route_template(request),
                outcome=AuditOutcome.refused,
                reason_code=ReasonCode.role_not_allowed,
                reference_id=request_id(request),
            )
        )
        structlog.get_logger().warning("access.refused", reason=ReasonCode.role_not_allowed.value)
        raise Forbidden()

    return dependency
