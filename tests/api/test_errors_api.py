"""HTTP tests against the running API (`make dev` locally, `Procfile.ci` in CI).

They use Playwright's APIRequestContext and import nothing from the app, so they test what a
client sees (quality-testing-playwright.md).
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from playwright.sync_api import APIRequestContext, Playwright

from packages.domain.errors import ErrorEnvelope
from packages.platform.settings import load_settings

BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
LEAK_MARKERS = ("Traceback", 'File "', "SELECT ", "pymongo", "mongodb://", "uvicorn")


@pytest.fixture(scope="module")
def api(playwright: Playwright) -> Iterator[APIRequestContext]:
    context = playwright.request.new_context(base_url=BASE_URL)
    yield context
    context.dispose()


def test_FR_000_08_login_validation_error_shape_matches_openapi(api: APIRequestContext) -> None:
    response = api.post("/api/v1/auth/login", data={"username": "bad name", "password": ""})

    body = ErrorEnvelope.model_validate(response.json())
    assert response.status == 422
    assert body.error.code.value == "validation_failed"
    assert {f.field for f in body.error.fields} == {"body.username", "body.password"}
    assert body.error.reference_id == response.headers["x-request-id"]


def test_FR_000_08_error_responses_leak_no_internals(api: APIRequestContext) -> None:
    responses = [
        api.get("/api/v1/nope"),
        api.post("/api/v1/health"),
        api.post("/api/v1/auth/login", data={"username": "x"}),
        api.get("/api/v1/auth/me"),
    ]

    for response in responses:
        assert response.status >= 400
        ErrorEnvelope.model_validate(response.json())
        assert not any(marker in response.text() for marker in LEAK_MARKERS)


def test_FR_000_06_me_unauthenticated_401_shape(api: APIRequestContext) -> None:
    response = api.get("/api/v1/auth/me")

    assert response.status == 401
    assert response.json()["error"]["code"] == "auth.required"


def test_FR_000_21_login_with_demo_account_then_me_then_logout(api: APIRequestContext) -> None:
    password = load_settings().DEMO_PASSWORD.get_secret_value()

    login = api.post("/api/v1/auth/login", data={"username": "caseworker1", "password": password})
    me = api.get("/api/v1/auth/me")
    logout = api.post("/api/v1/auth/logout")
    after = api.get("/api/v1/auth/me")

    assert login.status == 200
    assert login.json()["user"]["role"] == "caseworker"
    assert me.json()["user"]["username"] == "caseworker1"
    assert logout.status == 204
    assert after.status == 401
