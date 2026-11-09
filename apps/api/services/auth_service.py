from __future__ import annotations

from packages.domain.audit import (
    ActorType,
    AuditAction,
    AuditLogEntry,
    AuditOutcome,
    ReasonCode,
)
from packages.domain.auth import LoginRequest, Principal, SessionInfo
from packages.platform.audit import AuditWriter
from packages.platform.auth.identity import IdentityProvider
from packages.platform.auth.tokens import issue_user_token
from packages.platform.clock import Clock
from packages.platform.errors import AuthRequired, InvalidCredentials
from packages.platform.settings import Settings


async def login(
    body: LoginRequest,
    *,
    identity: IdentityProvider,
    audit: AuditWriter,
    settings: Settings,
    clock: Clock,
    reference_id: str,
) -> tuple[str, SessionInfo]:
    """Sign in. The audit entry never holds the typed username (FR-000-23, FR-000-11)."""
    user = await identity.authenticate(body.username, body.password)
    if user is None:
        await audit.write(
            AuditLogEntry(
                occurred_at=clock.now(),
                actor_type=ActorType.anonymous,
                action=AuditAction.sign_in,
                outcome=AuditOutcome.failure,
                reason_code=ReasonCode.invalid_credentials,
                reference_id=reference_id,
            )
        )
        raise InvalidCredentials()
    token, expires_at = issue_user_token(user, settings, clock)
    await audit.write(
        AuditLogEntry(
            occurred_at=clock.now(),
            actor_type=ActorType.user,
            actor_id=str(user.id),
            actor_role=user.role,
            action=AuditAction.sign_in,
            outcome=AuditOutcome.success,
            reference_id=reference_id,
        )
    )
    return token, SessionInfo(user=user, expires_at=expires_at)


async def me(principal: Principal, identity: IdentityProvider) -> SessionInfo:
    user = await identity.get_user(principal.user_id)
    if user is None:
        raise AuthRequired()
    return SessionInfo(user=user, expires_at=principal.session_expires_at)
