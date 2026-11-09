from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response

from apps.api.dependencies.auth import SESSION_COOKIE, current_principal, request_id
from apps.api.dependencies.db import get_audit_writer, get_clock, get_identity, get_settings
from apps.api.services import auth_service
from packages.domain.auth import LoginRequest, Principal, SessionInfo
from packages.domain.errors import ErrorEnvelope
from packages.platform.audit import AuditWriter
from packages.platform.auth.identity import IdentityProvider
from packages.platform.clock import Clock
from packages.platform.settings import AppEnv, Settings

router = APIRouter(prefix="/auth", tags=["auth"])

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {
        "model": ErrorEnvelope,
        "description": "auth.required, auth.session_expired or auth.invalid_credentials",
    },
    422: {"model": ErrorEnvelope, "description": "validation_failed"},
}


@router.post("/login", operation_id="login", responses=ERROR_RESPONSES)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    identity: Annotated[IdentityProvider, Depends(get_identity)],
    audit: Annotated[AuditWriter, Depends(get_audit_writer)],
    settings: Annotated[Settings, Depends(get_settings)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> SessionInfo:
    token, session = await auth_service.login(
        body,
        identity=identity,
        audit=audit,
        settings=settings,
        clock=clock,
        reference_id=request_id(request),
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="strict",
        path="/api",
        # Secure cookies are only stored over https, so tie the flag to the web origin's scheme.
        secure=settings.APP_ENV is AppEnv.prod and settings.WEB_ORIGIN.startswith("https://"),
        max_age=settings.SESSION_TTL_HOURS * 3600,
    )
    return session


@router.post("/logout", operation_id="logout", status_code=204, responses=ERROR_RESPONSES)
async def logout(
    response: Response, _principal: Annotated[Principal, Depends(current_principal)]
) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/api")


@router.get("/me", operation_id="getCurrentSession", responses=ERROR_RESPONSES)
async def me(
    principal: Annotated[Principal, Depends(current_principal)],
    identity: Annotated[IdentityProvider, Depends(get_identity)],
) -> SessionInfo:
    return await auth_service.me(principal, identity)
