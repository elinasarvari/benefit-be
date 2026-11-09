"""Health (process alive) and readiness (dependencies reachable) checks (FR-000-09).

No `temporalio` import here (BA-7): the Temporal check lives in the apps that use it.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from packages.domain.health import (
    CheckStatus,
    HealthReport,
    ReadinessCheck,
    ReadinessReport,
    ReadinessStatus,
)
from packages.platform.mongo import make_client
from packages.platform.version import VERSION

PROBE_TIMEOUT_S = 2.0


class Check(Protocol):
    name: str
    required: bool

    async def probe(self) -> CheckStatus: ...


async def run_check(check: Check) -> ReadinessCheck:
    """Run one probe. A failure or timeout is `down`, or `degraded` for an optional check."""
    failed = CheckStatus.down if check.required else CheckStatus.degraded
    try:
        status = await asyncio.wait_for(check.probe(), timeout=PROBE_TIMEOUT_S)
    except Exception:  # noqa: BLE001 - any probe failure means the dependency is not usable
        status = failed
    if status is not CheckStatus.ok:
        status = failed
    return ReadinessCheck(name=check.name, status=status, required=check.required)


async def build_report(component: str, checks: list[Check]) -> ReadinessReport:
    results = await asyncio.gather(*(run_check(check) for check in checks))
    return ReadinessReport.from_checks(component, list(results))


class MongoCheck:
    """MongoDB is reachable and the database carries the synthetic marker (FR-000-14)."""

    name = "mongo"
    required = True

    def __init__(self, url: str, database: str) -> None:
        self._url, self._database = url, database

    async def probe(self) -> CheckStatus:
        client = make_client(self._url)
        try:
            marker = await client[self._database]["_meta"].find_one({"_id": "dataset"})
        finally:
            await client.close()
        return CheckStatus.ok if marker and marker.get("synthetic") is True else CheckStatus.down


class HttpCheck:
    def __init__(self, name: str, url: str, *, required: bool) -> None:
        self.name, self._url, self.required = name, url, required

    async def probe(self) -> CheckStatus:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_S) as client:
            response = await client.get(self._url)
        return CheckStatus.ok if response.status_code == 200 else CheckStatus.down


def health_report(component: str) -> HealthReport:
    return HealthReport(component=component, version=VERSION)


def readiness_response(report: ReadinessReport) -> JSONResponse:
    code = 503 if report.status is ReadinessStatus.not_ready else 200
    return JSONResponse(report.model_dump(mode="json"), status_code=code)


def build_health_app(component: str, checks: list[Check]) -> FastAPI:
    """A tiny app for processes with no HTTP server of their own, such as the worker."""
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health() -> HealthReport:
        return health_report(component)

    @app.get("/ready")
    async def ready() -> JSONResponse:
        return readiness_response(await build_report(component, checks))

    return app


def serve_health(component: str, port: int, checks: list[Check]) -> uvicorn.Server:
    """Create a server for `/health` and `/ready`. The caller runs `await server.serve()`."""
    config = uvicorn.Config(
        build_health_app(component, checks), host="127.0.0.1", port=port, log_config=None
    )
    return uvicorn.Server(config)
