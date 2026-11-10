from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from apps.api.dependencies.db import get_audit_writer, get_clock, get_identity
from apps.api.main import create_app
from packages.domain.audit import ActorType, AuditAction, AuditOutcome, ReasonCode
from packages.domain.errors import ErrorCode
from packages.platform.audit import InMemoryAuditWriter
from packages.platform.auth.tokens import issue_system_credential
from packages.platform.clock import FixedClock
from packages.platform.settings import Settings
from tests.apps.api.fakes import PASSWORD, FakeIdentity

T0 = datetime(2026, 5, 4, 15, 0, tzinfo=UTC)


class Harness:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.clock = FixedClock(T0)
        self.identity = FakeIdentity()
        self.audit = InMemoryAuditWriter()
        app = create_app(settings)
        app.dependency_overrides[get_identity] = lambda: self.identity
        app.dependency_overrides[get_audit_writer] = lambda: self.audit
        app.dependency_overrides[get_clock] = lambda: self.clock
        self.client = TestClient(app)

    def login(self, username: str = "caseworker1", password: str = PASSWORD):  # type: ignore[no-untyped-def]
        return self.client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )


@pytest.fixture
def harness(settings: Settings) -> Harness:
    return Harness(settings)


def test_FR_000_21_login_success_sets_httponly_samesite_strict_cookie(harness: Harness) -> None:
    response = harness.login()

    cookie = response.headers["set-cookie"].lower()
    assert response.status_code == 200
    assert "benefit_session=" in cookie
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert "path=/api" in cookie
    body = response.json()
    assert body["user"]["role"] == "caseworker"
    assert body["user"]["username"] == "caseworker1"
    assert "password" not in response.text


def test_FR_000_21_unknown_user_and_wrong_password_identical_401(harness: Harness) -> None:
    unknown = harness.login("nobody", PASSWORD)
    wrong = harness.login("caseworker1", "wrong-password")

    for response in (unknown, wrong):
        assert response.status_code == 401
        assert response.json()["error"]["code"] == ErrorCode.auth_invalid_credentials.value
    first, second = unknown.json()["error"], wrong.json()["error"]
    assert {k: v for k, v in first.items() if k != "reference_id"} == {
        k: v for k, v in second.items() if k != "reference_id"
    }


def test_FR_000_06_me_without_cookie_returns_auth_required(harness: Harness) -> None:
    response = harness.client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.required"


def test_FR_000_05_me_returns_the_signed_in_user_and_expiry(harness: Harness) -> None:
    harness.login("supervisor1")

    response = harness.client.get("/api/v1/auth/me")

    assert response.status_code == 200
    assert response.json()["user"]["role"] == "supervisor"
    assert response.json()["expires_at"].startswith("2026-05-04T23:00:00")


def test_FR_000_07_expired_session_returns_session_expired(harness: Harness) -> None:
    harness.login()
    harness.clock.advance_to(T0 + timedelta(hours=8))

    response = harness.client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.session_expired"


def test_FR_000_22_system_credential_rejected_on_user_routes(harness: Harness) -> None:
    token = issue_system_credential(
        uuid.uuid4(), "case-1", "step", timedelta(seconds=60), harness.settings, harness.clock
    )
    harness.client.cookies.set("benefit_session", token, path="/api")

    response = harness.client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.required"


def test_logout_clears_cookie_returns_204(harness: Harness) -> None:
    harness.login()

    response = harness.client.post("/api/v1/auth/logout")

    assert response.status_code == 204
    assert "benefit_session=" in response.headers["set-cookie"]
    assert "max-age=0" in response.headers["set-cookie"].lower()
    assert harness.client.get("/api/v1/auth/me").status_code == 401


def test_logout_requires_a_session(harness: Harness) -> None:
    assert harness.client.post("/api/v1/auth/logout").status_code == 401


def test_login_validation_error_names_fields_without_echoing_values(harness: Harness) -> None:
    response = harness.client.post(
        "/api/v1/auth/login", json={"username": "x y", "password": "p4ss"}
    )

    assert response.status_code == 422
    assert [f["field"] for f in response.json()["error"]["fields"]] == ["body.username"]
    assert "p4ss" not in response.text


def test_FR_000_23_sign_in_success_and_failure_audited_without_username(harness: Harness) -> None:
    harness.login("caseworker1")
    harness.login("typed-username-secret", "wrong")

    success, failure = harness.audit.entries
    assert (success.action, success.outcome, success.actor_type) == (
        AuditAction.sign_in,
        AuditOutcome.success,
        ActorType.user,
    )
    assert success.actor_id is not None
    assert (failure.action, failure.outcome, failure.actor_type) == (
        AuditAction.sign_in,
        AuditOutcome.failure,
        ActorType.anonymous,
    )
    assert failure.reason_code is ReasonCode.invalid_credentials
    dumped = json.dumps([e.model_dump(mode="json") for e in harness.audit.entries])
    assert "typed-username-secret" not in dumped
    assert "caseworker1" not in dumped
