"""A tiny app that exercises the error handlers and middleware without any real routes."""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel, Field

from apps.api.errors import register_error_handlers
from apps.api.middleware.request_context import RequestContextMiddleware
from packages.domain.errors import ErrorCode
from packages.platform.errors import AppError


class ProbeBody(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


def build_probe_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)

    @app.post("/probe")
    def probe(body: ProbeBody) -> dict[str, str]:
        return {"user": body.username}

    @app.get("/probe/app-error/{code}")
    def app_error(code: str) -> None:
        raise AppError.for_code(ErrorCode(code))

    @app.get("/probe/boom")
    def boom() -> None:
        raise RuntimeError("SELECT secret FROM internal_table; host=db.internal")

    @app.get("/probe/items/{item_id}")
    def item(item_id: str) -> dict[str, str]:
        return {"id": item_id}

    return app
