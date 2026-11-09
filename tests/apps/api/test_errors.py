from __future__ import annotations

import io
import json
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from packages.domain.errors import ErrorCode, ErrorEnvelope
from packages.platform.logging import clear_context, configure_logging
from packages.platform.settings import Settings
from tests.apps.api.probe import build_probe_app

STATUS_FOR_CODE = {
    ErrorCode.auth_required: 401,
    ErrorCode.auth_session_expired: 401,
    ErrorCode.auth_invalid_credentials: 401,
    ErrorCode.auth_forbidden: 403,
    ErrorCode.not_found: 404,
    ErrorCode.conflict: 409,
    ErrorCode.validation_failed: 422,
    ErrorCode.internal_error: 500,
    ErrorCode.dependency_unavailable: 503,
}


@pytest.fixture
def client() -> TestClient:
    return TestClient(build_probe_app(), raise_server_exceptions=False)


@pytest.fixture
def log_stream(settings: Settings) -> Iterator[io.StringIO]:
    buffer = io.StringIO()
    configure_logging(settings, "api", stream=buffer)
    yield buffer
    clear_context()


def test_FR_000_08_validation_error_has_code_message_fields(client: TestClient) -> None:
    response = client.post("/probe", json={"password": "hunter2-secret"})

    body = ErrorEnvelope.model_validate(response.json())
    assert response.status_code == 422
    assert body.error.code is ErrorCode.validation_failed
    assert [f.field for f in body.error.fields] == ["body.username"]
    assert body.error.reference_id == response.headers["X-Request-ID"]
    assert "hunter2-secret" not in response.text


@pytest.mark.parametrize("code", list(ErrorCode))
def test_app_error_maps_to_status(client: TestClient, code: ErrorCode) -> None:
    response = client.get(f"/probe/app-error/{code.value}")

    body = ErrorEnvelope.model_validate(response.json())
    assert response.status_code == STATUS_FOR_CODE[code]
    assert body.error.code is code
    assert body.error.fields == []


def test_unknown_route_uses_the_error_envelope(client: TestClient) -> None:
    response = client.get("/nope")

    body = ErrorEnvelope.model_validate(response.json())
    assert response.status_code == 404
    assert body.error.code is ErrorCode.not_found


def test_FR_000_08_unexpected_error_returns_generic_message_with_reference(
    client: TestClient, log_stream: io.StringIO
) -> None:
    response = client.get("/probe/boom")

    body = ErrorEnvelope.model_validate(response.json())
    reference_id = body.error.reference_id
    assert response.status_code == 500
    assert body.error.code is ErrorCode.internal_error
    assert body.error.message == "Something went wrong."
    assert response.headers["X-Request-ID"] == reference_id
    for leaked in ("SELECT", "internal_table", "db.internal", "Traceback", "RuntimeError"):
        assert leaked not in response.text
    logged = [
        json.loads(line)
        for line in log_stream.getvalue().splitlines()
        if reference_id in line and "error.unhandled" in line
    ]
    assert len(logged) == 1
    assert "RuntimeError" in logged[0]["exception"]
    assert uuid.UUID(logged[0]["request_id"])
