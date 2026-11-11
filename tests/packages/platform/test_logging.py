from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator

import pytest
import structlog

from packages.platform.logging import bind_context, clear_context, configure_logging
from packages.platform.settings import Settings


@pytest.fixture
def stream(settings: Settings) -> Iterator[io.StringIO]:
    buffer = io.StringIO()
    configure_logging(settings, "api", stream=buffer)
    yield buffer
    clear_context()


def lines(buffer: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in buffer.getvalue().splitlines() if line.strip()]


def test_FR_000_10_log_includes_request_case_workflow_ids(stream: io.StringIO) -> None:
    bind_context(request_id="req-1", case_id="case-1", workflow_id="case-case-1")

    structlog.get_logger().info("request.completed", status=200)

    record = lines(stream)[0]
    assert record["event"] == "request.completed"
    assert record["level"] == "info"
    assert record["component"] == "api"
    assert record["request_id"] == "req-1"
    assert record["case_id"] == "case-1"
    assert record["workflow_id"] == "case-case-1"
    assert str(record["timestamp"]).endswith("Z")


def test_standard_library_logs_use_the_same_json_format(stream: io.StringIO) -> None:
    bind_context(request_id="req-2")

    logging.getLogger("uvicorn.error").warning("worker booted")

    record = lines(stream)[0]
    assert record["event"] == "worker booted"
    assert record["level"] == "warning"
    assert record["request_id"] == "req-2"


def test_clear_context_removes_bound_ids(stream: io.StringIO) -> None:
    bind_context(request_id="req-3")
    clear_context()

    structlog.get_logger().info("after.clear")

    assert "request_id" not in lines(stream)[0]


MASKED_KEYS = [
    "name", "first_name", "last_name", "full_name", "display_name", "username", "address",
    "street", "city", "zip", "dob", "date_of_birth", "birth_date", "ssn", "identity_number",
    "id_number", "document_text", "text", "content", "body", "password", "password_hash",
    "token", "tool_credential", "authorization", "cookie", "set-cookie", "api_key", "secret",
]  # fmt: skip
PLANTED = "Jane-Q-Example 1980-02-03 123 Maple Street"
JWT_LIKE = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl"


@pytest.mark.parametrize("key", MASKED_KEYS)
def test_FR_000_11_personal_data_masked(stream: io.StringIO, key: str) -> None:
    log = structlog.get_logger()

    log.info("probe", **{key: PLANTED, "nested": {key.upper(): [PLANTED]}, "status": 200})

    record = lines(stream)[0]
    assert PLANTED not in stream.getvalue()
    assert record[key] == "***"
    assert record["nested"] == {key.upper(): "***"}
    assert record["status"] == 200


FORM_KEYS = [
    "employer", "relationship", "members", "form", "jobs", "line1", "line2", "postal_code",
    "citizenship_status", "declared_monthly_gross",
]  # fmt: skip


@pytest.mark.parametrize("key", FORM_KEYS)
def test_FR_001_24_submit_log_line_has_no_form_fields(stream: io.StringIO, key: str) -> None:
    structlog.get_logger().info(
        "case.submitted", **{key: PLANTED, "nested": {key: [PLANTED]}, "created": True}
    )

    record = lines(stream)[0]
    assert PLANTED not in stream.getvalue()
    assert record[key] == "***"
    assert record["nested"] == {key: "***"}
    assert record["created"] is True


@pytest.mark.parametrize(
    "value", ["123-45-6789", "123456789", f"bearer {JWT_LIKE}", "failed for ssn 123-45-6789 in x"]
)
def test_FR_000_11_patterns_masked_inside_any_string(stream: io.StringIO, value: str) -> None:
    structlog.get_logger().info("probe", detail=value)

    out = stream.getvalue()
    assert "123-45-6789" not in out and "123456789" not in out and JWT_LIKE not in out


def test_FR_000_11_exception_text_is_masked(stream: io.StringIO) -> None:
    try:
        raise ValueError("bad value for ssn 123-45-6789")
    except ValueError:
        structlog.get_logger().exception("boom")

    record = lines(stream)[0]
    assert "123-45-6789" not in stream.getvalue()
    assert "ValueError" in str(record["exception"])


def test_FR_000_11_standard_library_messages_are_masked_too(stream: io.StringIO) -> None:
    logging.getLogger("uvicorn.error").warning("login for 123-45-6789 failed")

    assert "123-45-6789" not in stream.getvalue()


def test_ids_and_other_fields_are_not_masked(stream: io.StringIO) -> None:
    bind_context(request_id="3f0c2a9e-4b1d-4e55-9a51-7f2f8f5f1c11", case_id="c-1")

    structlog.get_logger().info("probe", route="/api/v1/health", duration_ms=1.5)

    record = lines(stream)[0]
    assert record["request_id"] == "3f0c2a9e-4b1d-4e55-9a51-7f2f8f5f1c11"
    assert record["case_id"] == "c-1"
    assert record["route"] == "/api/v1/health"
