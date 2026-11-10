from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.api.routers.health import get_checks
from packages.domain.health import CheckStatus
from packages.platform.health import Check
from packages.platform.settings import Settings


class FakeCheck:
    def __init__(self, name: str, status: CheckStatus, *, required: bool) -> None:
        self.name, self.required, self._status = name, required, status

    async def probe(self) -> CheckStatus:
        return self._status


def client_with(settings: Settings, checks: list[Check]) -> TestClient:
    app = create_app(settings)
    app.dependency_overrides[get_checks] = lambda: checks
    return TestClient(app)


def test_FR_000_09_health_returns_ok_with_synthetic_flag(settings: Settings) -> None:
    response = TestClient(create_app(settings)).get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["component"] == "api"
    assert body["status"] == "ok"
    assert body["synthetic_data"] is True
    assert body["version"]


def test_FR_000_09_ready_returns_ready_when_all_checks_ok(settings: Settings) -> None:
    client = client_with(
        settings,
        [
            FakeCheck("mongo", CheckStatus.ok, required=True),
            FakeCheck("temporal", CheckStatus.ok, required=True),
        ],
    )

    response = client.get("/api/v1/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


@pytest.mark.parametrize(
    ("status", "code", "required"),
    [
        (CheckStatus.down, 503, True),
        (CheckStatus.down, 200, False),
    ],
)
def test_FR_000_09_ready_status_follows_required_checks(
    settings: Settings, status: CheckStatus, code: int, required: bool
) -> None:
    client = client_with(
        settings,
        [
            FakeCheck("mongo", CheckStatus.ok, required=True),
            FakeCheck("dep", status, required=required),
        ],
    )

    response = client.get("/api/v1/ready")

    assert response.status_code == code
    assert response.json()["status"] == ("not_ready" if required else "degraded")


def test_FR_000_09_health_and_ready_need_no_session(settings: Settings) -> None:
    client = client_with(settings, [FakeCheck("mongo", CheckStatus.ok, required=True)])

    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/ready").status_code == 200
