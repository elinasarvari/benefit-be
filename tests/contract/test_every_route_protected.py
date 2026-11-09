from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from packages.platform.settings import Settings

OPEN_ROUTES = {
    ("GET", "/api/v1/health"),
    ("GET", "/api/v1/ready"),
    ("POST", "/api/v1/auth/login"),
}


def routes(settings: Settings) -> list[tuple[str, str]]:
    """Every operation the API documents. The OpenAPI schema is the contract, so use it."""
    paths = create_app(settings).openapi()["paths"]
    return [
        (method.upper(), path)
        for path, operations in paths.items()
        for method in operations
        if path.startswith("/api/v1")
    ]


def test_FR_000_06_every_non_health_route_requires_session(settings: Settings) -> None:
    client = TestClient(create_app(settings))
    protected = [r for r in routes(settings) if r not in OPEN_ROUTES]

    assert protected, "there must be protected routes to check"
    for method, path in protected:
        concrete = path.replace("{", "").replace("}", "")
        concrete = "/".join(
            str(uuid.uuid4()) if part and part[0].isalpha() and "_id" in part else part
            for part in concrete.split("/")
        )
        response = client.request(method, concrete, json={})
        assert response.status_code == 401, f"{method} {path} answered {response.status_code}"
        assert response.json()["error"]["code"] == "auth.required", f"{method} {path}"


@pytest.mark.parametrize(("method", "path"), sorted(OPEN_ROUTES))
def test_the_only_open_routes_are_health_ready_and_login(
    settings: Settings, method: str, path: str
) -> None:
    assert (method, path) in routes(settings)
