from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI

from apps.api.dependencies.auth import SESSION_COOKIE
from apps.api.errors import register_error_handlers
from apps.api.middleware.request_context import RequestContextMiddleware
from apps.api.routers import applications, auth, cases, health
from packages.platform.logging import configure_logging
from packages.platform.mongo import make_client
from packages.platform.settings import Settings, load_settings
from packages.platform.version import VERSION

API_PREFIX = "/api/v1"
OPEN_OPERATIONS = [
    (f"{API_PREFIX}/health", "get"),
    (f"{API_PREFIX}/ready", "get"),
    (f"{API_PREFIX}/auth/login", "post"),
]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API. Run it with `uvicorn apps.api.main:create_app --factory`."""
    settings = settings or load_settings()
    configure_logging(settings, "api")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        client = getattr(app.state, "mongo", None)
        if client is not None:
            await client.close()

    # No docs or openapi route: the contract is the committed openapi/openapi.json, and every
    # served route is under /api/v1 and needs a session, except health, ready and login.
    app = FastAPI(
        title="benefit-be",
        version=VERSION,
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.mongo = make_client(settings.MONGO_URL_APP.get_secret_value())  # connects on use
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)

    api = APIRouter(prefix=API_PREFIX)
    api.include_router(health.router)
    api.include_router(auth.router)
    api.include_router(applications.router)
    api.include_router(cases.router)
    app.include_router(api)
    _declare_security(app)
    return app


def _declare_security(app: FastAPI) -> None:
    """Put the session-cookie scheme in the OpenAPI document (FR-000-26, api-generation.md)."""
    build = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            schema = build()
            schema.setdefault("components", {})["securitySchemes"] = {
                "sessionCookie": {"type": "apiKey", "in": "cookie", "name": SESSION_COOKIE}
            }
            schema["security"] = [{"sessionCookie": []}]
            for path, method in OPEN_OPERATIONS:
                schema["paths"][path][method]["security"] = []
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]
