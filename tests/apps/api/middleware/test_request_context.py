from __future__ import annotations

import io
import json
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from packages.platform.logging import clear_context, configure_logging
from packages.platform.settings import Settings
from tests.apps.api.probe import build_probe_app


@pytest.fixture
def log_stream(settings: Settings) -> Iterator[io.StringIO]:
    buffer = io.StringIO()
    configure_logging(settings, "api", stream=buffer)
    yield buffer
    clear_context()


def test_FR_000_10_request_id_generated_echoed_and_logged(log_stream: io.StringIO) -> None:
    client = TestClient(build_probe_app())

    response = client.get("/probe/items/abc123")

    request_id = response.headers["X-Request-ID"]
    assert uuid.UUID(request_id)
    record = next(
        json.loads(line)
        for line in log_stream.getvalue().splitlines()
        if "request.completed" in line
    )
    assert record["request_id"] == request_id
    assert record["route"] == "/probe/items/{item_id}"
    assert record["method"] == "GET"
    assert record["status"] == 200
    assert "abc123" not in json.dumps(record)


def test_valid_incoming_request_id_is_kept() -> None:
    client = TestClient(build_probe_app())
    incoming = str(uuid.uuid4())

    response = client.get("/probe/items/1", headers={"X-Request-ID": incoming})

    assert response.headers["X-Request-ID"] == incoming


def test_request_id_invalid_header_replaced() -> None:
    client = TestClient(build_probe_app())

    response = client.get("/probe/items/1", headers={"X-Request-ID": "not-a-uuid; drop table"})

    assert uuid.UUID(response.headers["X-Request-ID"])
