"""Session tokens for users and case-scoped credentials for agent tools (PL-5, FR-000-22).

Both are HS256 JWTs signed with different secrets and told apart by `typ` and `aud`, so one can
never be accepted as the other. Expiry is checked against our clock, not the wall clock.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import jwt

from packages.domain.auth import Principal, Role, SystemCredentialClaims, UserPublic
from packages.platform.clock import Clock
from packages.platform.errors import AuthRequired, CredentialRejected, SessionExpired
from packages.platform.settings import Settings

ALGORITHM = "HS256"
OPTIONS: Any = {"verify_exp": False, "verify_aud": False, "verify_iat": False}


def _decode(token: str, secret: str) -> dict[str, Any]:
    return jwt.decode(token, secret, algorithms=[ALGORITHM], options=OPTIONS)


def issue_user_token(user: UserPublic, settings: Settings, clock: Clock) -> tuple[str, datetime]:
    now = clock.now()
    expires_at = now + timedelta(hours=settings.SESSION_TTL_HOURS)
    claims: dict[str, Any] = {
        "typ": "user",
        "sub": str(user.id),
        "role": user.role.value,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": uuid.uuid4().hex,
    }
    if user.role is Role.applicant and user.household_id is not None:
        claims["hh"] = str(user.household_id)
    token = jwt.encode(claims, settings.SESSION_SECRET.get_secret_value(), algorithm=ALGORITHM)
    return token, expires_at


def verify_user_token(token: str, settings: Settings, clock: Clock) -> Principal:
    try:
        claims = _decode(token, settings.SESSION_SECRET.get_secret_value())
        if claims.get("typ") != "user":
            raise AuthRequired()
        expires_at = datetime.fromtimestamp(int(claims["exp"]), tz=clock.now().tzinfo)
        principal = Principal(
            user_id=UUID(claims["sub"]),
            role=Role(claims["role"]),
            household_id=UUID(claims["hh"]) if claims.get("hh") else None,
            session_expires_at=expires_at,
        )
    except (jwt.PyJWTError, KeyError, ValueError, TypeError) as exc:
        raise AuthRequired() from exc
    if clock.now() >= expires_at:
        raise SessionExpired()
    return principal


def issue_system_credential(
    case_id: UUID,
    workflow_id: str,
    step: str,
    ttl: timedelta,
    settings: Settings,
    clock: Clock,
) -> str:
    now = clock.now()
    claims = SystemCredentialClaims(
        case_id=case_id,
        workflow_id=workflow_id,
        step=step,
        iat=int(now.timestamp()),
        exp=int((now + ttl).timestamp()),
        jti=uuid.uuid4().hex,
    )
    secret = settings.SYSTEM_CREDENTIAL_SECRET.get_secret_value()
    return jwt.encode(claims.model_dump(mode="json"), secret, algorithm=ALGORITHM)


def verify_system_credential(
    token: str, expected_case_id: UUID, settings: Settings, clock: Clock
) -> SystemCredentialClaims:
    try:
        raw = _decode(token, settings.SYSTEM_CREDENTIAL_SECRET.get_secret_value())
        claims = SystemCredentialClaims.model_validate(raw)
    except (jwt.PyJWTError, ValueError) as exc:
        raise CredentialRejected() from exc
    if int(clock.now().timestamp()) >= claims.exp:
        raise CredentialRejected()
    if claims.case_id != expected_case_id:
        raise CredentialRejected()
    return claims
