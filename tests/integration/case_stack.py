"""The real case stack for [qa] integration tests (001; TC-68–TC-77).

The API app (in this event loop, through httpx's ASGI transport), the local replica-set MongoDB
(`benefit_test`, `benefit_audit_test`) and the local Temporal dev server. The worker runs either
in-process (`case_worker`) or as its own OS process (`worker_process`), with the real
`record_case_change` and the 001 stubs answering by the seed's stub results
(db/seeds/generate.py, research R1). Each test uses its own task queue, so workflows left on the
dev server by other runs are never picked up. All data is synthetic (PR-3).
"""

from __future__ import annotations

import asyncio
import os
import signal
import socket
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from temporalio.client import Client, WorkflowHandle
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from apps.api.main import create_app
from apps.worker.activities.case_record import CaseRecordActivities
from apps.worker.activities.determination import DeterminationActivities, FixedStubResults
from apps.worker.activities.verification import verify_documents
from apps.worker.repositories.cases import CaseRepository
from apps.worker.workflows.case import CaseWorkflow
from db.seeds.generate import STUB_RESULTS
from db.seeds.ids import HOUSEHOLD_A, HOUSEHOLD_B, HOUSEHOLD_C, seeded_id
from db.seeds.users import DEMO_ACCOUNTS
from packages.domain.auth import Role, UserPublic
from packages.domain.case import CaseState, workflow_id_for
from packages.platform.audit import MongoAuditWriter
from packages.platform.auth.tokens import issue_user_token
from packages.platform.clock import SystemClock
from packages.platform.mongo import Client as MongoClient
from packages.platform.mongo import make_client
from packages.platform.settings import Settings
from tests.apps.api.forms import body_dict

API = "/api/v1"
REPO_ROOT = Path(__file__).resolve().parents[2]
ELIGIBLE_HOUSEHOLD = HOUSEHOLD_A  # applicant1
INELIGIBLE_HOUSEHOLD = HOUSEHOLD_B  # applicant2
CANNOT_DETERMINE_HOUSEHOLD = HOUSEHOLD_C  # applicant3
SYNTH_REASON = "SYNTH-REASON-123"
WAIT_S = 30.0


def seeded_user(username: str) -> UserPublic:
    for name, display_name, role, household_id in DEMO_ACCOUNTS:
        if name == username:
            return UserPublic(
                id=seeded_id(f"user-{username}"),
                username=name,
                display_name=display_name,
                role=role,
                household_id=household_id,
            )
    raise KeyError(username)


def applicant_for(household_id: uuid.UUID) -> UserPublic:
    for name, _, role, household in DEMO_ACCOUNTS:
        if role is Role.applicant and household == household_id:
            return seeded_user(name)
    raise KeyError(household_id)


CASEWORKER = "caseworker1"


def stack_settings(base: Settings, task_queue: str, *, demo_profile: bool) -> Settings:
    return base.model_copy(update={"TEMPORAL_TASK_QUEUE": task_queue, "DEMO_PROFILE": demo_profile})


def new_task_queue(name: str) -> str:
    return f"test-{name}-{uuid.uuid4().hex[:8]}"


async def connect_temporal(settings: Settings) -> Client:
    try:
        return await asyncio.wait_for(
            Client.connect(
                settings.TEMPORAL_ADDRESS,
                namespace=settings.TEMPORAL_NAMESPACE,
                data_converter=pydantic_data_converter,
            ),
            timeout=5,
        )
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"Temporal is not reachable at {settings.TEMPORAL_ADDRESS}: run `make dev` or "
            f"`temporal server start-dev` ({type(exc).__name__})"
        )


@asynccontextmanager
async def mongo(settings: Settings) -> AsyncIterator[MongoClient]:
    client = make_client(settings.MONGO_URL_OWNER.get_secret_value())
    try:
        yield client
    finally:
        await client.close()


def case_activities(settings: Settings, client: MongoClient) -> list[Callable[..., Any]]:
    """The worker's case activities, with the stub answering by the seeded household result."""
    audit = MongoAuditWriter(client, settings.MONGO_AUDIT_DB)
    record = CaseRecordActivities(CaseRepository(client, settings.MONGO_DB, audit))
    determination = DeterminationActivities(FixedStubResults(STUB_RESULTS))
    return [record.record_case_change, verify_documents, determination.determine_eligibility]


@asynccontextmanager
async def case_worker(settings: Settings, temporal: Client) -> AsyncIterator[None]:
    client = make_client(settings.MONGO_URL_APP.get_secret_value())
    try:
        async with Worker(
            temporal,
            task_queue=settings.TEMPORAL_TASK_QUEUE,
            workflows=[CaseWorkflow],
            activities=case_activities(settings, client),
        ):
            yield
    finally:
        await client.close()


class Api:
    """The real API app in this event loop, signed in by minting a session for a seeded user."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.app = create_app(settings)
        self._clients: list[httpx.AsyncClient] = []

    def client_for(self, user: UserPublic) -> httpx.AsyncClient:
        from apps.api.dependencies.auth import SESSION_COOKIE

        token, _ = issue_user_token(user, self.settings, SystemClock())
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://test",
            cookies={SESSION_COOKIE: token},
        )
        self._clients.append(client)
        return client

    async def close(self) -> None:
        for client in self._clients:
            await client.aclose()
        await self.app.state.mongo.close()


@asynccontextmanager
async def api(settings: Settings) -> AsyncIterator[Api]:
    stack = Api(settings)
    try:
        yield stack
    finally:
        await stack.close()


async def submit(client: httpx.AsyncClient, reference: uuid.UUID | None = None) -> httpx.Response:
    body = body_dict(reference or uuid.uuid4())
    return await client.post(f"{API}/applications", json=body)


async def get_case(client: httpx.AsyncClient, case_id: str) -> dict[str, Any]:
    response = await client.get(f"{API}/cases/{case_id}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def act(
    client: httpx.AsyncClient,
    case_id: str,
    task_id: str,
    action: str,
    reason: str | None = None,
) -> httpx.Response:
    body = {"action": action, "reason": reason, "request_id": str(uuid.uuid4())}
    return await client.post(f"{API}/cases/{case_id}/review-tasks/{task_id}/actions", json=body)


async def eventually[T](
    probe: Callable[[], Awaitable[T | None]], what: str, within: float = WAIT_S
) -> T:
    deadline = asyncio.get_running_loop().time() + within
    while True:
        value = await probe()
        if value is not None:
            return value
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.1)


async def wait_for_state(
    client: httpx.AsyncClient, case_id: str, state: CaseState
) -> dict[str, Any]:
    async def probe() -> dict[str, Any] | None:
        body = await get_case(client, case_id)
        return body if body["state"] == state.value else None

    return await eventually(probe, f"case {case_id} in {state.value}")


def handle_for(temporal: Client, case_id: str) -> WorkflowHandle[Any, Any]:
    return temporal.get_workflow_handle(workflow_id_for(uuid.UUID(case_id)))


async def documents(
    client: MongoClient, settings: Settings, collection: str, case_id: str
) -> list[dict[str, Any]]:
    cursor = client[settings.MONGO_DB][collection].find({"case_id": uuid.UUID(case_id)})
    return [doc async for doc in cursor.sort("_id", 1)]


async def events(client: MongoClient, settings: Settings, case_id: str) -> list[dict[str, Any]]:
    cursor = client[settings.MONGO_DB]["case_events"].find({"case_id": uuid.UUID(case_id)})
    return [doc async for doc in cursor.sort("seq", 1)]


async def case_document(client: MongoClient, settings: Settings, case_id: str) -> dict[str, Any]:
    doc = await client[settings.MONGO_DB]["cases"].find_one({"_id": uuid.UUID(case_id)})
    assert doc is not None, f"no cases document for {case_id}"
    return doc


def as_date(value: datetime | date) -> date:
    """Calendar dates are stored as midnight-UTC BSON dates."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC).date() if value.tzinfo is None else value.date()
    return value


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def expected_coverage(received_on: date) -> tuple[date, date]:
    """Written out independently of `case_states.coverage_for` (FR-001-22): first day of the
    receipt month to the last day of the twelfth month."""
    start = received_on.replace(day=1)
    month = start.month + 12
    first_after = date(start.year + (month - 1) // 12, (month - 1) % 12 + 1, 1)
    return start, first_after - timedelta(days=1)


# --- A worker as its own OS process (TC-75, TW-16) -----------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


@dataclass
class WorkerProcess:
    process: asyncio.subprocess.Process

    async def kill(self) -> None:
        """SIGKILL: no graceful shutdown, no activity completion (the crash TW-16 asks for)."""
        if self.process.returncode is None:
            self.process.send_signal(signal.SIGKILL)
        await asyncio.wait_for(self.process.wait(), 10)


@asynccontextmanager
async def worker_process(settings: Settings) -> AsyncIterator[WorkerProcess]:
    env = {
        **os.environ,
        "APP_ENV": "test",
        "LLM_MODE": "fake",
        "EMBEDDER": "fake",
        "RERANKER": "fake",
        "TRACING_ENABLED": "false",
        "MONGO_DB": settings.MONGO_DB,
        "MONGO_AUDIT_DB": settings.MONGO_AUDIT_DB,
        "MONGO_URL_APP": settings.MONGO_URL_APP.get_secret_value(),
        "MONGO_URL_OWNER": settings.MONGO_URL_OWNER.get_secret_value(),
        "SESSION_SECRET": settings.SESSION_SECRET.get_secret_value(),
        "SYSTEM_CREDENTIAL_SECRET": settings.SYSTEM_CREDENTIAL_SECRET.get_secret_value(),
        "TEMPORAL_ADDRESS": settings.TEMPORAL_ADDRESS,
        "TEMPORAL_NAMESPACE": settings.TEMPORAL_NAMESPACE,
        "TEMPORAL_TASK_QUEUE": settings.TEMPORAL_TASK_QUEUE,
        "WORKER_HEALTH_PORT": str(_free_port()),
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.integration.case_worker_process",
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    worker = WorkerProcess(process)
    try:
        yield worker
    finally:
        await worker.kill()
