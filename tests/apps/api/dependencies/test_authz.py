from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from apps.api.dependencies.auth import require_roles
from apps.api.dependencies.db import get_audit_writer, get_clock
from apps.api.errors import register_error_handlers
from apps.api.middleware.request_context import RequestContextMiddleware
from packages.domain.audit import AuditAction, AuditOutcome, ReasonCode
from packages.domain.auth import Principal, Role
from packages.platform.audit import InMemoryAuditWriter
from packages.platform.auth.tokens import issue_system_credential, issue_user_token
from packages.platform.clock import FixedClock
from packages.platform.settings import Settings
from tests.apps.api.fakes import make_users

T0 = datetime(2026, 5, 4, 15, 0, tzinfo=UTC)
ROUTE = "/probe/caseworker-only"


@pytest.fixture
def audit() -> InMemoryAuditWriter:
    return InMemoryAuditWriter()


@pytest.fixture
def client(settings: Settings, audit: InMemoryAuditWriter) -> TestClient:
    app = FastAPI()
    app.state.settings = settings
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)
    app.dependency_overrides[get_audit_writer] = lambda: audit
    app.dependency_overrides[get_clock] = lambda: FixedClock(T0)

    @app.get(ROUTE)
    async def caseworker_only(
        principal: Annotated[Principal, Depends(require_roles(Role.caseworker))],
    ) -> dict[str, str]:
        return {"role": principal.role.value}

    return TestClient(app)


def sign_in(client: TestClient, settings: Settings, role: Role) -> None:
    user = next(u for u, _ in make_users().values() if u.role is role)
    token, _ = issue_user_token(user, settings, FixedClock(T0))
    client.cookies.set("benefit_session", token)


@pytest.mark.parametrize(
    ("role", "status"),
    [(Role.caseworker, 200), (Role.supervisor, 200), (Role.applicant, 403)],
)
def test_FR_000_05_supervisor_allowed_where_caseworker_allowed(
    client: TestClient, settings: Settings, role: Role, status: int
) -> None:
    sign_in(client, settings, role)

    assert client.get(ROUTE).status_code == status


def test_FR_000_06_route_without_session_is_refused(client: TestClient) -> None:
    response = client.get(ROUTE)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.required"


def test_FR_000_22_system_credential_cannot_use_a_role_route(
    client: TestClient, settings: Settings
) -> None:
    token = issue_system_credential(
        uuid.uuid4(), "case-1", "step", timedelta(seconds=60), settings, FixedClock(T0)
    )
    client.cookies.set("benefit_session", token)

    assert client.get(ROUTE).status_code == 401


def test_FR_000_23_refused_access_writes_access_refused_audit_entry(
    client: TestClient, settings: Settings, audit: InMemoryAuditWriter
) -> None:
    sign_in(client, settings, Role.applicant)

    response = client.get(ROUTE)

    (entry,) = audit.entries
    assert response.status_code == 403
    assert entry.action is AuditAction.access_refused
    assert entry.outcome is AuditOutcome.refused
    assert entry.reason_code is ReasonCode.role_not_allowed
    assert entry.target_type == "route"
    assert entry.target_id == ROUTE
    assert entry.reference_id == response.headers["X-Request-ID"]
