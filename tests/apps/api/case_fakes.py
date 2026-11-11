"""In-memory fakes for the case repository and the CaseWorkflow gateway (001 API tests)."""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import TypeVar
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from apps.api.dependencies.auth import SESSION_COOKIE
from apps.api.dependencies.cases import get_case_repository, get_case_workflows, get_user_names
from apps.api.dependencies.db import get_audit_writer, get_clock
from apps.api.main import create_app
from apps.api.repositories.cases import CaseAlreadyExists, HouseholdHasOpenCase
from packages.domain.audit import AuditLogEntry
from packages.domain.auth import Principal, Role, UserPublic
from packages.domain.case import (
    AllowedActions,
    Case,
    CaseEvent,
    CaseStart,
    CaseState,
    CaseworkerAction,
    CaseworkerActionRequest,
    CoveragePeriod,
    OpenReviewTask,
    StoredApplication,
)
from packages.platform.audit import AuditWriter, InMemoryAuditWriter
from packages.platform.auth.tokens import issue_user_token
from packages.platform.clock import FixedClock
from packages.platform.errors import DependencyUnavailable, Forbidden, NotFound
from packages.platform.scope import Scope
from packages.platform.settings import Settings
from tests.apps.api.fakes import make_users

T = TypeVar("T")
FIXED_NOW = datetime(2026, 3, 17, 15, 0, tzinfo=UTC)  # 10:00 in Chicago


def principal(role: Role, household: UUID | None = None, user: UUID | None = None) -> Principal:
    return Principal(
        user_id=user or uuid4(),
        role=role,
        household_id=household,
        session_expires_at=FIXED_NOW,
    )


def _visible(scope: Scope, household_id: UUID) -> bool:
    return scope.role is not Role.applicant or scope.household_id == household_id


class FakeCaseRepository:
    """Enforces the same guards as the migration's indexes (unique _id, one open case)."""

    def __init__(self) -> None:
        self.cases: dict[UUID, Case] = {}
        self.coverage: list[CoveragePeriod] = []
        self.tasks: dict[UUID, OpenReviewTask] = {}
        self.actions: dict[UUID, CaseworkerAction] = {}
        self.applications: dict[UUID, StoredApplication] = {}
        self.events: list[CaseEvent] = []
        self.inserts = 0
        self.application_writes = 0
        self.calls: list[tuple[str, Scope]] = []  # every read, with the scope it carried
        self.in_transaction = False
        self.application_in_transaction: list[bool] = []

    async def run_in_transaction(self, work: Callable[[FakeCaseRepository], Awaitable[T]]) -> T:
        snapshot = copy.deepcopy((self.cases, self.applications))
        self.in_transaction = True
        try:
            return await work(self)
        except BaseException:
            self.cases, self.applications = snapshot
            raise
        finally:
            self.in_transaction = False

    async def find_case(self, scope: Scope, case_id: UUID) -> Case | None:
        self.calls.append(("find_case", scope))
        case = self.cases.get(case_id)
        return case if case is not None and _visible(scope, case.household_id) else None

    async def active_coverage(
        self, scope: Scope, household_id: UUID, day: date
    ) -> CoveragePeriod | None:
        self.calls.append(("active_coverage", scope))
        return next(
            (c for c in self.coverage if c.household_id == household_id and c.end_date >= day), None
        )

    async def insert_case(self, scope: Scope, case: Case) -> None:
        if not _visible(scope, case.household_id):
            raise Forbidden()
        if case.id in self.cases:
            raise CaseAlreadyExists()
        if any(c.household_id == case.household_id and c.open for c in self.cases.values()):
            raise HouseholdHasOpenCase()
        self.cases[case.id] = case
        self.inserts += 1

    async def insert_application(self, scope: Scope, application: StoredApplication) -> None:
        if not _visible(scope, application.household_id):
            raise Forbidden()
        self.application_writes += 1
        self.application_in_transaction.append(self.in_transaction)
        self.applications[application.id] = application

    async def find_open_case_id(self, scope: Scope, household_id: UUID) -> UUID | None:
        self.calls.append(("find_open_case_id", scope))
        return next(
            (
                c.id
                for c in self.cases.values()
                if c.household_id == household_id and c.open and _visible(scope, c.household_id)
            ),
            None,
        )

    async def coverage_for_case(self, scope: Scope, case_id: UUID) -> CoveragePeriod | None:
        self.calls.append(("coverage_for_case", scope))
        return next(
            (c for c in self.coverage if c.case_id == case_id and _visible(scope, c.household_id)),
            None,
        )

    async def list_cases(
        self,
        scope: Scope,
        *,
        state: CaseState | None,
        after: tuple[date, UUID] | None,
        limit: int,
    ) -> list[Case]:
        self.calls.append(("list_cases", scope))
        rows = [
            c
            for c in self.cases.values()
            if _visible(scope, c.household_id) and (c.open if state is None else c.state is state)
        ]
        rows.sort(key=lambda c: (c.decision_deadline, c.id))
        if after is not None:
            rows = [c for c in rows if (c.decision_deadline, c.id) > after]
        return rows[:limit]

    async def list_events(
        self, scope: Scope, case_id: UUID, *, after_seq: int, limit: int
    ) -> list[CaseEvent]:
        self.calls.append(("list_events", scope))
        rows = [e for e in self.events if e.case_id == case_id and e.seq > after_seq]
        return sorted(rows, key=lambda e: e.seq)[:limit]

    async def open_review_task(self, scope: Scope, case_id: UUID) -> OpenReviewTask | None:
        self.calls.append(("open_review_task", scope))
        return self.tasks.get(case_id)

    async def record_rejected_action(
        self, scope: Scope, action: CaseworkerAction, entry: AuditLogEntry, audit: AuditWriter
    ) -> None:
        if not _visible(scope, action.household_id):
            raise Forbidden()
        if action.id in self.actions:
            return  # a retried request: already recorded
        self.actions[action.id] = action
        await audit.write(entry)


class FakeUserNames:
    def __init__(self) -> None:
        self.names: dict[UUID, str] = {}
        self.queries: list[list[UUID]] = []

    async def display_names(self, ids: list[UUID]) -> dict[UUID, str]:
        self.queries.append(ids)
        return {i: self.names[i] for i in ids if i in self.names}


class FakeCaseWorkflows:
    def __init__(self) -> None:
        self.started: dict[str, CaseStart] = {}
        self.start_calls = 0
        self.fail_next_start = False
        self.allowed = AllowedActions()
        self.signals: list[tuple[UUID, CaseworkerActionRequest]] = []
        self.completed = False  # a completed (decided) workflow can't take signals

    async def start(self, start: CaseStart) -> None:
        self.start_calls += 1
        if self.fail_next_start:
            self.fail_next_start = False
            raise DependencyUnavailable()
        self.started.setdefault(f"case-{start.case_id}", start)  # already started = success

    async def allowed_actions(self, case_id: UUID) -> AllowedActions:
        if self.allowed is None:
            raise NotFound()
        return self.allowed

    async def signal_action(
        self, case_id: UUID, request: CaseworkerActionRequest, *, best_effort: bool = False
    ) -> bool:
        if self.completed:
            if best_effort:
                return False
            raise NotFound()
        self.signals.append((case_id, request))
        return True


class CaseApiHarness:
    """The real app with the case repository, workflows, audit and clock replaced by fakes."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.clock = FixedClock(FIXED_NOW)
        self.repo = FakeCaseRepository()
        self.workflows = FakeCaseWorkflows()
        self.audit = InMemoryAuditWriter()
        self.names = FakeUserNames()
        self.users = {u.role: u for u, _ in make_users().values()}
        app = create_app(settings)
        app.dependency_overrides[get_case_repository] = lambda: self.repo
        app.dependency_overrides[get_case_workflows] = lambda: self.workflows
        app.dependency_overrides[get_user_names] = lambda: self.names
        app.dependency_overrides[get_audit_writer] = lambda: self.audit
        app.dependency_overrides[get_clock] = lambda: self.clock
        self.client = TestClient(app)

    def sign_in(self, role: Role) -> UserPublic:
        user = self.users[role]
        token, _ = issue_user_token(user, self.settings, self.clock)
        self.client.cookies.set(SESSION_COOKIE, token)
        return user

    def sign_out(self) -> None:
        self.client.cookies.clear()
