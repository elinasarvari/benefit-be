from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from apps.api.errors import register_error_handlers
from apps.api.middleware.request_context import RequestContextMiddleware
from apps.api.routers import auth, health
from packages.platform.logging import configure_logging
from packages.platform.settings import Settings, load_settings
from packages.platform.version import VERSION

API_PREFIX = "/api/v1"


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

    app = FastAPI(
        title="benefit-be",
        version=VERSION,
        openapi_url=f"{API_PREFIX}/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)

    api = APIRouter(prefix=API_PREFIX)
    api.include_router(health.router)
    api.include_router(auth.router)
    app.include_router(api)
    return app
