"""Temporal worker entry point: `python -m apps.worker.main`.

Start-up order: settings, logging, health server, Temporal connection, worker. Temporal being down
is not a start-up failure: the health server reports the worker as not ready while it retries.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import structlog
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from apps.worker.activities.agent_step import AgentStepActivities, smoke_tool
from apps.worker.activities.case_record import CaseRecordActivities
from apps.worker.activities.determination import DeterminationActivities
from apps.worker.activities.redeterminations_due import DueRedeterminationActivities
from apps.worker.activities.renewal import RenewalActivities
from apps.worker.activities.review_tasks import ReviewTaskActivities
from apps.worker.activities.smoke import smoke_step
from apps.worker.activities.snapshot import SnapshotCapture
from apps.worker.activities.verification import verify_documents
from apps.worker.health import WorkerState, worker_checks
from apps.worker.interceptors import ContextInterceptor
from apps.worker.repositories.cases import CaseRepository
from apps.worker.repositories.caseworker_actions import CaseworkerActionRepository
from apps.worker.repositories.coverage_periods import MongoCaseDates, MongoCoveragePeriods
from apps.worker.repositories.determinations import MongoDeterminationStore
from apps.worker.repositories.due_cases import DueCases
from apps.worker.repositories.notices import MongoNoticeStore
from apps.worker.repositories.reference_records import MongoReferenceRecords
from apps.worker.repositories.review_tasks import ReviewTaskRepository
from apps.worker.repositories.snapshots import MongoSnapshotSource
from apps.worker.repositories.verified_facts import SeededVerifications
from apps.worker.schedules import ensure_redeterminations_due_schedule
from apps.worker.workflows.case import CaseWorkflow
from apps.worker.workflows.redeterminations_due import RedeterminationsDueWorkflow
from apps.worker.workflows.smoke import SmokeWorkflow
from packages.platform.audit import MongoAuditWriter
from packages.platform.clock import Clock, SystemClock
from packages.platform.health import serve_health
from packages.platform.logging import configure_logging
from packages.platform.mongo import Client as MongoClient
from packages.platform.mongo import make_client
from packages.platform.settings import Settings, load_settings

log = structlog.get_logger()
MAX_BACKOFF_S = 30.0
CONNECT_TIMEOUT_S = 5.0


async def connect_with_backoff(settings: Settings) -> Client:
    delay = 1.0
    while True:
        try:
            return await asyncio.wait_for(
                Client.connect(
                    settings.TEMPORAL_ADDRESS,
                    namespace=settings.TEMPORAL_NAMESPACE,
                    data_converter=pydantic_data_converter,
                ),
                timeout=CONNECT_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001 - keep retrying; readiness reports not ready meanwhile
            log.warning("temporal.connect.failed", error=type(exc).__name__, retry_in_s=delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_BACKOFF_S)


# CaseWorkflow replaces 010's ReviewHarnessWorkflow, which stood in for it until 001 landed and
# is kept for tests only (010 T009, T018; ADR-001-11).
WORKFLOWS = [SmokeWorkflow, CaseWorkflow, RedeterminationsDueWorkflow]


def review_activities(settings: Settings, mongo: MongoClient, clock: Clock) -> ReviewTaskActivities:
    return ReviewTaskActivities(
        ReviewTaskRepository(mongo[settings.MONGO_DB], clock),
        CaseworkerActionRepository(
            mongo, settings.MONGO_DB, MongoAuditWriter(mongo, settings.MONGO_AUDIT_DB)
        ),
    )


def determination_activities(
    settings: Settings, mongo: MongoClient, clock: Clock
) -> DeterminationActivities:
    """The rules engine over snapshots, interim seeded verifications and the determination store."""
    capture = SnapshotCapture(
        MongoSnapshotSource(mongo, settings.MONGO_DB),
        SeededVerifications(mongo, settings.MONGO_DB),
        clock,
    )
    return DeterminationActivities(
        capture, MongoDeterminationStore(mongo, settings.MONGO_DB), clock
    )


def redetermination_activities(
    settings: Settings, mongo: MongoClient, clock: Clock, audit: MongoAuditWriter
) -> tuple[RenewalActivities, DueRedeterminationActivities]:
    """007: the renewal check and request over the reference data set, and the daily scan."""
    db = settings.MONGO_DB
    periods = MongoCoveragePeriods(mongo, db)
    renewal = RenewalActivities(
        periods,
        MongoSnapshotSource(mongo, db),
        MongoReferenceRecords(mongo, db),
        MongoNoticeStore(mongo, db),
        MongoCaseDates(mongo, db),
        clock,
    )
    due = DueRedeterminationActivities(
        periods, DueCases(mongo, db, audit), clock, settings.DEMO_PROFILE
    )
    return renewal, due


def build_activities(settings: Settings, clock: Clock, mongo: MongoClient) -> list[Any]:
    """Every activity, built once per worker process (TW-12)."""
    agent_steps = AgentStepActivities(settings, clock, smoke_tool(settings, clock))
    audit = MongoAuditWriter(mongo, settings.MONGO_AUDIT_DB)
    case_record = CaseRecordActivities(CaseRepository(mongo, settings.MONGO_DB, audit))
    determination = determination_activities(settings, mongo, clock)
    # 010's review-task activities, for the review loop CaseWorkflow composes (010 T018).
    review = review_activities(settings, mongo, clock)
    renewal, due = redetermination_activities(settings, mongo, clock, audit)
    return [
        smoke_step,
        agent_steps.run,
        case_record.record_case_change,
        verify_documents,
        determination.determine_eligibility,
        review.upsert_review_task,
        review.set_review_flag,
        review.close_review_task,
        review.record_caseworker_action,
        renewal.check_renewal,
        renewal.prepare_renewal_request,
        renewal.deliver_renewal_request,
        due.open_due_redeterminations,
    ]


async def run_worker(settings: Settings) -> None:
    state = WorkerState()
    server = serve_health("worker", settings.WORKER_HEALTH_PORT, worker_checks(settings, state))
    health_task = asyncio.create_task(server.serve())
    mongo = make_client(settings.MONGO_URL_APP.get_secret_value())
    try:
        client = await connect_with_backoff(settings)
        worker = Worker(
            client,
            task_queue=settings.TEMPORAL_TASK_QUEUE,
            workflows=WORKFLOWS,
            activities=build_activities(settings, SystemClock(), mongo),
            interceptors=[ContextInterceptor()],
        )
        await ensure_redeterminations_due_schedule(
            client,
            settings.TEMPORAL_TASK_QUEUE,
            enabled=settings.REDETERMINATION_SCHEDULE_ENABLED,
        )
        state.polling = True
        log.info("worker.started", task_queue=settings.TEMPORAL_TASK_QUEUE)
        await worker.run()
    finally:
        state.polling = False
        server.should_exit = True
        await health_task
        await mongo.close()


def main() -> int:
    settings = load_settings()
    configure_logging(settings, "worker")
    asyncio.run(run_worker(settings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
