from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.main import create_app
from packages.platform.settings import Settings
from tests.integration.fixtures import rebuild

UNUSED = "mongodb://app:secret-pw@127.0.0.1:9/?serverSelectionTimeoutMS=300"


def statuses(response_json: dict[str, object]) -> dict[str, str]:
    return {c["name"]: c["status"] for c in response_json["checks"]}  # type: ignore[index, union-attr]


async def test_FR_000_09_mongo_down_not_ready_health_ok(integration_settings: Settings) -> None:
    settings = integration_settings.model_copy(
        update={"MONGO_URL_APP": integration_settings.MONGO_URL_APP.__class__(UNUSED)}
    )
    client = TestClient(create_app(settings))

    health = client.get("/api/v1/health")
    ready = client.get("/api/v1/ready")

    assert health.status_code == 200
    assert ready.status_code == 503
    assert ready.json()["status"] == "not_ready"
    assert statuses(ready.json())["mongo"] == "down"


async def test_FR_000_09_ready_response_has_no_connection_details(
    integration_settings: Settings,
) -> None:
    settings = integration_settings.model_copy(
        update={"MONGO_URL_APP": integration_settings.MONGO_URL_APP.__class__(UNUSED)}
    )

    text = TestClient(create_app(settings)).get("/api/v1/ready").text

    for leaked in ("secret-pw", "mongodb://", "127.0.0.1", ":9", "7233", "localhost"):
        assert leaked not in text


async def test_FR_000_09_ready_when_dependencies_are_up(integration_settings: Settings) -> None:
    await rebuild(integration_settings)

    ready = TestClient(create_app(integration_settings)).get("/api/v1/ready")

    assert ready.status_code == 200, ready.text
    assert ready.json()["status"] == "ready"


async def test_FR_000_28_tracing_down_reports_degraded(integration_settings: Settings) -> None:
    await rebuild(integration_settings)
    settings = integration_settings.model_copy(
        update={
            "TRACING_ENABLED": True,
            "TRACING_OTLP_ENDPOINT": "http://127.0.0.1:9/v1/traces",
        }
    )

    ready = TestClient(create_app(settings)).get("/api/v1/ready")

    assert ready.status_code == 200
    assert ready.json()["status"] == "degraded"
    assert statuses(ready.json())["tracing"] == "degraded"
