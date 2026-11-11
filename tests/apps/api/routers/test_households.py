"""007a: household page, on-demand redetermination start and case notices (TC-38 to TC-48).

The real app with in-memory repositories and workflow gateway, so they need no running stack.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from uuid import UUID

import pytest

from apps.api.dependencies.households import get_household_repository, get_notice_repository
from apps.api.schemas.mvp import CaseNoticeView, HouseholdMemberView
from packages.domain.audit import AuditAction, AuditOutcome
from packages.domain.auth import Role
from packages.domain.case import (
    ActionType,
    AllowedActions,
    Case,
    CaseState,
    CaseType,
    CoveragePeriod,
    CoverageStatus,
    NoticeType,
    Relationship,
    ReviewReason,
    allowed_actions_for,
    redetermination_case_id,
)
from packages.domain.notice import NoticeStatus, RequestedItem, RequestedItemKind
from packages.platform.scope import Scope
from packages.platform.settings import Settings
from tests.apps.api.case_fakes import FIXED_NOW, CaseApiHarness
from tests.apps.api.fakes import HOUSEHOLD

TODAY = FIXED_NOW.date()
TASK = UUID("00000000-0000-4000-8000-0000000000e1")


class FakeHouseholdRepository:
    def __init__(self, cases_repo: object) -> None:
        self.members: dict[UUID, list[HouseholdMemberView]] = {}
        self.periods: list[CoveragePeriod] = []
        self._cases = cases_repo

    async def members_of(self, scope: Scope, household_id: UUID) -> list[HouseholdMemberView]:
        return self.members.get(household_id, [])

    async def coverage_periods_of(self, scope: Scope, household_id: UUID) -> list[CoveragePeriod]:
        return [p for p in self.periods if p.household_id == household_id]

    async def find_period(
        self, scope: Scope, household_id: UUID, period_id: UUID
    ) -> CoveragePeriod | None:
        return next(
            (p for p in self.periods if p.household_id == household_id and p.id == period_id),
            None,
        )

    async def cases_of(self, scope: Scope, household_id: UUID) -> list[Case]:
        return [c for c in self._cases.cases.values() if c.household_id == household_id]  # type: ignore[attr-defined]


class FakeNoticeRepository:
    def __init__(self) -> None:
        self.notices: dict[UUID, list[CaseNoticeView]] = {}

    async def list_for_case(self, scope: Scope, case_id: UUID, limit: int) -> list[CaseNoticeView]:
        rows = sorted(self.notices.get(case_id, []), key=lambda n: n.created_at, reverse=True)
        return rows[:limit]


@pytest.fixture
def harness(settings: Settings) -> CaseApiHarness:
    return CaseApiHarness(settings)


@pytest.fixture
def households(harness: CaseApiHarness) -> FakeHouseholdRepository:
    repo = FakeHouseholdRepository(harness.repo)
    harness.client.app.dependency_overrides[get_household_repository] = lambda: repo  # type: ignore[attr-defined]
    return repo


@pytest.fixture
def notices(harness: CaseApiHarness) -> FakeNoticeRepository:
    repo = FakeNoticeRepository()
    harness.client.app.dependency_overrides[get_notice_repository] = lambda: repo  # type: ignore[attr-defined]
    return repo


def put_period(
    households: FakeHouseholdRepository,
    *,
    household: UUID = HOUSEHOLD,
    status: CoverageStatus = CoverageStatus.active,
) -> CoveragePeriod:
    period_id = uuid.uuid4()
    period = CoveragePeriod(
        id=period_id,
        household_id=household,
        case_id=uuid.uuid4(),
        start_date=TODAY - timedelta(days=300),
        end_date=TODAY + timedelta(days=40),
        status=status,
    )
    households.periods.append(period)
    return period


def start_url(household: UUID = HOUSEHOLD) -> str:
    return f"/api/v1/households/{household}/redeterminations"


def put_open_application(harness: CaseApiHarness) -> Case:
    from tests.apps.api.routers.test_cases import put_case

    return put_case(harness, state=CaseState.under_review)


def test_FR_007_10_start_creates_redetermination_for_active_period_201(
    harness: CaseApiHarness, households: FakeHouseholdRepository
) -> None:
    period = put_period(households)
    harness.sign_in(Role.caseworker)

    response = harness.client.post(start_url(), json={"coverage_period_id": str(period.id)})

    case_id = redetermination_case_id(HOUSEHOLD, period.id)
    assert response.status_code == 201
    assert response.json() == {"case_id": str(case_id), "created": True}
    stored = harness.repo.cases[case_id]
    assert stored.case_type is CaseType.redetermination
    assert stored.coverage_period_id == period.id
    assert harness.workflows.started[f"case-{case_id}"].coverage_period_id == period.id
    audited = [
        e
        for e in harness.audit.entries
        if e.action is AuditAction.redetermination_started_on_demand
    ]
    assert len(audited) == 1
    assert audited[0].target_id == str(case_id)
    assert audited[0].outcome is AuditOutcome.success


def test_FR_007_07_second_start_returns_same_case_200_created_false(
    harness: CaseApiHarness, households: FakeHouseholdRepository
) -> None:
    period = put_period(households)
    harness.sign_in(Role.caseworker)
    body = {"coverage_period_id": str(period.id)}
    first = harness.client.post(start_url(), json=body)

    again = harness.client.post(start_url(), json=body)
    harness.repo.cases[UUID(first.json()["case_id"])].open = False
    harness.repo.cases[UUID(first.json()["case_id"])].state = CaseState.decided
    period_renewed = period.model_copy(update={"status": CoverageStatus.renewed})
    households.periods = [period_renewed]
    after_decided = harness.client.post(start_url(), json=body)

    assert again.status_code == 200
    assert again.json() == {"case_id": first.json()["case_id"], "created": False}
    assert after_decided.status_code == 200
    assert after_decided.json()["case_id"] == first.json()["case_id"]
    assert harness.repo.inserts == 1
    audited = [
        e
        for e in harness.audit.entries
        if e.action is AuditAction.redetermination_started_on_demand
    ]
    assert len(audited) == 1


def test_FR_007_10_start_audit_failure_leaves_no_case_without_its_entry(
    harness: CaseApiHarness,
    households: FakeHouseholdRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    period = put_period(households)
    harness.sign_in(Role.caseworker)

    async def failing_write(entry: object, session: object = None) -> None:
        raise RuntimeError("audit store down")

    monkeypatch.setattr(harness.audit, "write", failing_write)
    response = harness.client.post(start_url(), json={"coverage_period_id": str(period.id)})

    assert response.status_code == 500
    assert harness.repo.cases == {}
    assert harness.workflows.start_calls == 0


def test_FR_007_10_open_case_refuses_with_link(
    harness: CaseApiHarness, households: FakeHouseholdRepository
) -> None:
    period = put_period(households)
    other = put_open_application(harness)
    harness.sign_in(Role.caseworker)

    response = harness.client.post(start_url(), json={"coverage_period_id": str(period.id)})

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "conflict"
    assert error["related_case_id"] == str(other.case_id)
    assert error["fields"][0]["type"] == "household_has_open_case"
    assert harness.workflows.start_calls == 0


@pytest.mark.parametrize("kind", ["renewed", "ended", "other_household", "unknown"])
def test_FR_007_10_period_of_other_household_or_not_active_is_409(
    harness: CaseApiHarness, households: FakeHouseholdRepository, kind: str
) -> None:
    if kind in ("renewed", "ended"):
        period_id = put_period(households, status=CoverageStatus(kind)).id
    elif kind == "other_household":
        period_id = put_period(households, household=uuid.uuid4()).id
    else:
        period_id = uuid.uuid4()
    harness.sign_in(Role.caseworker)

    response = harness.client.post(start_url(), json={"coverage_period_id": str(period_id)})

    assert response.status_code == 409
    assert response.json()["error"]["fields"][0]["type"] == "no_active_coverage_period"
    assert harness.repo.inserts == 0
    assert harness.workflows.start_calls == 0


def test_FR_007_10_temporal_unavailable_returns_503_and_retry_is_safe(
    harness: CaseApiHarness, households: FakeHouseholdRepository
) -> None:
    period = put_period(households)
    harness.sign_in(Role.caseworker)
    harness.workflows.fail_next_start = True
    body = {"coverage_period_id": str(period.id)}

    failed = harness.client.post(start_url(), json=body)
    retry = harness.client.post(start_url(), json=body)

    case_id = redetermination_case_id(HOUSEHOLD, period.id)
    assert failed.status_code == 503
    assert case_id in harness.repo.cases
    assert retry.status_code in (200, 201)
    assert retry.json()["case_id"] == str(case_id)
    assert f"case-{case_id}" in harness.workflows.started
    assert harness.repo.inserts == 1


def test_FR_007_10_applicant_start_is_403_and_audited(
    harness: CaseApiHarness, households: FakeHouseholdRepository
) -> None:
    period = put_period(households)
    harness.sign_in(Role.applicant)

    start = harness.client.post(start_url(), json={"coverage_period_id": str(period.id)})
    read = harness.client.get(f"/api/v1/households/{HOUSEHOLD}")

    assert start.status_code == read.status_code == 403
    refused = [e for e in harness.audit.entries if e.action is AuditAction.access_refused]
    assert len(refused) == 2
    assert harness.repo.inserts == 0


def test_FR_007_18_get_household_returns_members_periods_cases_with_running_link(
    harness: CaseApiHarness, households: FakeHouseholdRepository
) -> None:
    member = HouseholdMemberView(
        member_id=uuid.uuid4(),
        full_name="SYNTH-NAME Example",
        date_of_birth=date(1985, 6, 15),
        relationship=Relationship.self,
    )
    households.members[HOUSEHOLD] = [member]
    households.members[uuid.uuid4()] = [member]
    period = put_period(households)
    put_period(households, household=uuid.uuid4())
    harness.sign_in(Role.caseworker)
    started = harness.client.post(start_url(), json={"coverage_period_id": str(period.id)})

    response = harness.client.get(f"/api/v1/households/{HOUSEHOLD}")

    body = response.json()
    assert response.status_code == 200
    assert body["household_id"] == str(HOUSEHOLD)
    assert [m["member_id"] for m in body["members"]] == [str(member.member_id)]
    assert [p["coverage_period_id"] for p in body["coverage_periods"]] == [str(period.id)]
    assert (
        body["coverage_periods"][0]["running_redetermination_case_id"] == started.json()["case_id"]
    )
    assert [c["case_id"] for c in body["cases"]] == [started.json()["case_id"]]
    assert body["cases"][0]["case_type"] == "redetermination"


def test_FR_007_18_get_household_without_members_is_404_not_found(
    harness: CaseApiHarness, households: FakeHouseholdRepository
) -> None:
    harness.sign_in(Role.caseworker)

    response = harness.client.get(f"/api/v1/households/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert response.json()["error"]["reference_id"]


def make_notice(
    created_offset: int, status: NoticeStatus = NoticeStatus.pending_approval
) -> CaseNoticeView:
    return CaseNoticeView(
        notice_id=uuid.uuid4(),
        notice_type=NoticeType.request_for_information,
        status=status,
        items=[
            RequestedItem(
                item_id=uuid.uuid4(),
                kind=RequestedItemKind.confirm_income,
                member_id=uuid.uuid4(),
                job_id=uuid.uuid4(),
                label="SYNTH-LABEL confirm income",
            )
        ],
        due_case_date=None,
        created_at=FIXED_NOW + timedelta(minutes=created_offset),
        delivered_at=None,
    )


def test_FR_007_03_list_case_notices_returns_pending_request_items(
    harness: CaseApiHarness, notices: FakeNoticeRepository
) -> None:
    case = put_open_application(harness)
    notices.notices[case.case_id] = [make_notice(i) for i in range(25)]
    harness.sign_in(Role.caseworker)

    response = harness.client.get(f"/api/v1/cases/{case.case_id}/notices")

    items = response.json()["items"]
    assert response.status_code == 200
    assert len(items) == 20
    assert items[0]["created_at"] > items[-1]["created_at"]
    assert items[0]["status"] == "pending_approval"
    assert items[0]["items"][0]["kind"] == "confirm_income"
    assert "text" not in items[0]


def test_FR_007_03_list_case_notices_applicant_403_unknown_case_404(
    harness: CaseApiHarness, notices: FakeNoticeRepository
) -> None:
    case = put_open_application(harness)
    harness.sign_in(Role.applicant)
    applicant = harness.client.get(f"/api/v1/cases/{case.case_id}/notices")
    harness.sign_in(Role.caseworker)

    unknown = harness.client.get(f"/api/v1/cases/{uuid.uuid4()}/notices")

    assert applicant.status_code == 403
    assert unknown.status_code == 404


def test_FR_007_03_submit_approve_accepted_only_for_notice_approval_task(
    harness: CaseApiHarness,
) -> None:
    from tests.apps.api.routers.test_cases import actions_url, put_case

    case = put_case(harness, state=CaseState.under_review)
    harness.sign_in(Role.caseworker)
    body = {"action": "approve", "reason": None, "request_id": str(uuid.uuid4())}
    harness.workflows.allowed = AllowedActions(task_id=TASK, actions=[ActionType.approve])

    ok = harness.client.post(actions_url(case.case_id), json=body)
    other_task = harness.client.post(
        actions_url(case.case_id, uuid.uuid4()), json={**body, "request_id": str(uuid.uuid4())}
    )
    harness.workflows.allowed = AllowedActions(
        task_id=TASK, actions=[ActionType.approve_denial, ActionType.override_to_approve]
    )
    wrong_type = harness.client.post(
        actions_url(case.case_id), json={**body, "request_id": str(uuid.uuid4())}
    )

    assert ok.status_code == 202
    assert other_task.status_code == 409
    assert wrong_type.status_code == 409
    assert [s.action for _, s in harness.workflows.signals][:1] == [ActionType.approve]
    assert allowed_actions_for(ReviewReason.notice_approval) == [ActionType.approve]
