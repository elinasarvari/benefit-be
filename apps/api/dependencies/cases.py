from __future__ import annotations

import asyncio

from fastapi import FastAPI, Request
from temporalio.client import Client

from apps.api.dependencies.db import get_mongo, get_settings
from apps.api.repositories.cases import MongoCaseRepository
from apps.api.temporal_client import TemporalCaseWorkflows, connect_temporal


def get_case_repository(request: Request) -> MongoCaseRepository:
    return MongoCaseRepository(get_mongo(request), get_settings(request).MONGO_DB)


def get_case_workflows(request: Request) -> TemporalCaseWorkflows:
    """One Temporal client per process, connected on first use and retried if that failed."""
    app = request.app
    settings = get_settings(request)

    async def connect() -> Client:
        lock: asyncio.Lock = _lock(app)
        async with lock:
            client: Client | None = getattr(app.state, "temporal", None)
            if client is None:
                client = await connect_temporal(settings)
                app.state.temporal = client
            return client

    return TemporalCaseWorkflows(connect, settings.TEMPORAL_TASK_QUEUE)


def _lock(app: FastAPI) -> asyncio.Lock:
    lock: asyncio.Lock | None = getattr(app.state, "temporal_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        app.state.temporal_lock = lock
    return lock
