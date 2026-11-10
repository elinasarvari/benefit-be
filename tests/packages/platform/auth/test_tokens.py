from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from packages.domain.auth import Role, UserPublic
from packages.platform.auth.tokens import (
    issue_system_credential,
    issue_user_token,
    verify_system_credential,
    verify_user_token,
)
from packages.platform.clock import FixedClock
from packages.platform.errors import AuthRequired, CredentialRejected, SessionExpired
from packages.platform.settings import Settings

T0 = datetime(2026, 5, 4, 15, 0, tzinfo=UTC)


def make_user(role: Role = Role.caseworker) -> UserPublic:
    household = uuid.uuid4() if role is Role.applicant else None
    return UserPublic(
        id=uuid.uuid4(),
        username="caseworker1",
        display_name="Caseworker One",
        role=role,
        household_id=household,
    )


def test_FR_000_07_session_expires_after_configured_ttl(settings: Settings) -> None:
    clock = FixedClock(T0)
    token, expires_at = issue_user_token(make_user(), settings, clock)

    assert expires_at == T0 + timedelta(hours=8)
    clock.advance_to(T0 + timedelta(hours=8) - timedelta(seconds=1))
    assert verify_user_token(token, settings, clock).role is Role.caseworker
    clock.advance_to(T0 + timedelta(hours=8))
    with pytest.raises(SessionExpired):
        verify_user_token(token, settings, clock)


def test_FR_000_07_session_ttl_is_configurable(settings: Settings) -> None:
    short = settings.model_copy(update={"SESSION_TTL_HOURS": 2})
    clock = FixedClock(T0)
    token, expires_at = issue_user_token(make_user(), short, clock)

    assert expires_at == T0 + timedelta(hours=2)
    clock.advance_to(T0 + timedelta(hours=2))
    with pytest.raises(SessionExpired):
        verify_user_token(token, short, clock)


def test_applicant_token_carries_household_and_others_do_not(settings: Settings) -> None:
    clock = FixedClock(T0)
    applicant, caseworker = make_user(Role.applicant), make_user(Role.caseworker)

    applicant_token, _ = issue_user_token(applicant, settings, clock)
    caseworker_token, _ = issue_user_token(caseworker, settings, clock)

    assert (
        verify_user_token(applicant_token, settings, clock).household_id == applicant.household_id
    )
    assert verify_user_token(caseworker_token, settings, clock).household_id is None


def test_forged_or_garbage_session_token_is_rejected(settings: Settings) -> None:
    clock = FixedClock(T0)
    forged = jwt.encode(
        {
            "typ": "user",
            "sub": str(uuid.uuid4()),
            "role": "supervisor",
            "iat": 0,
            "exp": 9999999999,
        },
        "not-the-secret-not-the-secret-not-the-secret",
        algorithm="HS256",
    )

    for bad in (forged, "garbage", ""):
        with pytest.raises(AuthRequired):
            verify_user_token(bad, settings, clock)


def test_FR_000_22_credential_names_one_case_and_has_no_user_role(settings: Settings) -> None:
    clock = FixedClock(T0)
    case_id = uuid.uuid4()

    token = issue_system_credential(
        case_id, "case-1", "verify_documents", timedelta(seconds=120), settings, clock
    )

    claims = verify_system_credential(token, case_id, settings, clock)
    assert claims.case_id == case_id
    assert claims.exp - claims.iat == 120
    payload = jwt.decode(token, options={"verify_signature": False})
    assert "role" not in payload
    assert payload["typ"] == "system"
    assert payload["aud"] == "agent-tools"


def test_FR_000_22_credential_rejected_for_other_case(settings: Settings) -> None:
    clock = FixedClock(T0)
    token = issue_system_credential(
        uuid.uuid4(), "case-1", "step", timedelta(seconds=60), settings, clock
    )

    with pytest.raises(CredentialRejected):
        verify_system_credential(token, uuid.uuid4(), settings, clock)


def test_FR_000_22_credential_rejected_after_expiry(settings: Settings) -> None:
    clock = FixedClock(T0)
    case_id = uuid.uuid4()
    token = issue_system_credential(
        case_id, "case-1", "step", timedelta(seconds=60), settings, clock
    )

    clock.advance_to(T0 + timedelta(seconds=60))

    with pytest.raises(CredentialRejected):
        verify_system_credential(token, case_id, settings, clock)


def test_FR_000_22_new_credential_per_issue_has_a_new_id(settings: Settings) -> None:
    clock = FixedClock(T0)
    case_id = uuid.uuid4()

    first = issue_system_credential(case_id, "w", "s", timedelta(seconds=60), settings, clock)
    second = issue_system_credential(case_id, "w", "s", timedelta(seconds=60), settings, clock)

    assert verify_system_credential(first, case_id, settings, clock).jti != (
        verify_system_credential(second, case_id, settings, clock).jti
    )


def test_FR_000_22_user_token_rejected_by_system_verifier(settings: Settings) -> None:
    clock = FixedClock(T0)
    token, _ = issue_user_token(make_user(), settings, clock)

    with pytest.raises(CredentialRejected):
        verify_system_credential(token, uuid.uuid4(), settings, clock)


def test_FR_000_22_system_token_rejected_by_user_verifier(settings: Settings) -> None:
    clock = FixedClock(T0)
    token = issue_system_credential(
        uuid.uuid4(), "case-1", "step", timedelta(seconds=60), settings, clock
    )

    with pytest.raises(AuthRequired):
        verify_user_token(token, settings, clock)
