"""Case routes (001): list, detail, history, review action (TC-59–65, 95, 104, 108, 110, 111)."""

from __future__ import annotations

import io
import uuid
from datetime import date, timedelta
from uuid import UUID

import pytest

from packages.domain.audit import AuditAction, AuditOutcome
from packages.domain.auth import Role
from packages.domain.case import (
    ActionOutcome,
    ActionType,
    AllowedActions,
    Case,
    CaseClock,
    CaseEvent,
    CaseEventKind,
    CaseState,
    CaseType,
    EventActorType,
    OpenReviewTask,
    ReviewReason,
)
from packages.domain.review import RejectionCode
from packages.platform.clock import case_today
from packages.platform.logging import configure_logging
from packages.platform.settings import Settings
from tests.apps.api.case_fakes import FIXED_NOW, CaseApiHarness
from tests.apps.api.fakes import HOUSEHOLD

TASK = UUID("00000000-0000-4000-8000-0000000000e1")
BOTH = [ActionType.approve_denial, ActionType.override_to_approve]
SYNTH_REASON = "SYNTH-REASON-123"


@pytest.fixture
def harness(settings: Settings) -> CaseApiHarness:
    return CaseApiHarness(settings)


def put_case(
    harness: CaseApiHarness,
    *,
    age_days: int = 3,
    household: UUID = HOUSEHOLD,
    state: CaseState = CaseState.under_review,
) -> Case:
    real_start = FIXED_NOW - timedelta(days=age_days)
    clock = CaseClock(real_start=real_start, case_start=real_start, scale=1)
    received = case_today(clock, real_start)
    case_id = uuid.uuid4()
    case = Case(
        id=case_id,
        case_id=case_id,
        household_id=household,
        case_type=CaseType.application,
        submission_reference=str(uuid.uuid4()),
        state=state,
        open=state is not CaseState.decided,
        received_on=received,
        decision_deadline=received + timedelta(days=45),
        clock=clock,
        created_at=real_start,
        updated_at=real_start,
    )
    harness.repo.cases[case_id] = case
    return case


def actions_url(case_id: UUID, task_id: UUID = TASK) -> str:
    return f"/api/v1/cases/{case_id}/review-tasks/{task_id}/actions"


def action_body(action: str = "approve_denial", reason: str | None = None) -> dict[str, object]:
    return {"action": action, "reason": reason, "request_id": str(uuid.uuid4())}


def test_FR_001_09_review_action_rejects_applicant_role(harness: CaseApiHarness) -> None:
    case = put_case(harness)
    harness.workflows.allowed = AllowedActions(task_id=TASK, actions=BOTH)

    harness.sign_in(Role.applicant)
    applicant = harness.client.post(actions_url(case.case_id), json=action_body())
    harness.sign_out()
    anonymous = harness.client.post(actions_url(case.case_id), json=action_body())

    assert applicant.status_code == 403
    assert anonymous.status_code == 401
    assert harness.workflows.signals == []


@pytest.mark.parametrize("reason", [None, "", "x" * 501])
def test_FR_001_20_override_without_reason_returns_422(
    harness: CaseApiHarness, reason: str | None
) -> None:
    case = put_case(harness)
    harness.workflows.allowed = AllowedActions(task_id=TASK, actions=BOTH)
    harness.sign_in(Role.caseworker)

    response = harness.client.post(
        actions_url(case.case_id), json=action_body("override_to_approve", reason)
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    assert harness.workflows.signals == []


@pytest.mark.parametrize(
    "allowed",
    [
        AllowedActions(),  # no open task
        AllowedActions(task_id=uuid.uuid4(), actions=BOTH),  # another task is open
        AllowedActions(task_id=TASK, actions=[ActionType.override_to_approve]),
    ],
)
def test_FR_001_07_action_not_allowed_returns_409(
    harness: CaseApiHarness, allowed: AllowedActions
) -> None:
    case = put_case(harness)
    harness.workflows.allowed = allowed
    harness.sign_in(Role.caseworker)

    response = harness.client.post(actions_url(case.case_id), json=action_body())

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


def test_FR_001_02_action_on_decided_case_recorded_rejected_by_api(
    harness: CaseApiHarness,
) -> None:
    """TC-61, US1-AS5: the decided case's workflow has completed and can't take the signal, so
    the API records the refusal through the repository (ADR-001-04 Option B)."""
    case = put_case(harness, state=CaseState.decided)
    harness.workflows.allowed = AllowedActions()  # decided: no open task
    harness.workflows.completed = True
    user = harness.sign_in(Role.caseworker)
    body = action_body("override_to_approve", SYNTH_REASON)

    first = harness.client.post(actions_url(case.case_id), json=body)
    retried = harness.client.post(actions_url(case.case_id), json=body)  # same request_id

    assert (first.status_code, retried.status_code) == (409, 409)
    [recorded] = harness.repo.actions.values()
    assert str(recorded.action_id) == body["request_id"]
    assert (recorded.case_id, recorded.household_id) == (case.case_id, case.household_id)
    assert recorded.task_id == TASK
    assert recorded.action is ActionType.override_to_approve
    assert recorded.reviewer_id == user.id
    assert recorded.outcome is ActionOutcome.rejected
    assert recorded.rejection_code is RejectionCode.task_closed
    assert recorded.reviewer_role is Role.caseworker
    assert recorded.recorded_at == FIXED_NOW
    [entry] = harness.audit.entries  # one audit entry, even for the retried request
    assert entry.action is AuditAction.caseworker_action
    assert entry.outcome is AuditOutcome.refused
    assert (entry.actor_id, entry.target_id) == (str(user.id), str(case.case_id))
    assert entry.reference_id == body["request_id"]


def test_FR_001_02_refused_action_on_running_workflow_left_to_workflow(
    harness: CaseApiHarness,
) -> None:
    case = put_case(harness)
    harness.workflows.allowed = AllowedActions(task_id=TASK, actions=BOTH)
    harness.sign_in(Role.caseworker)

    response = harness.client.post(actions_url(case.case_id, uuid.uuid4()), json=action_body())

    assert response.status_code == 409
    assert len(harness.workflows.signals) == 1  # the workflow records `action_rejected`
    assert harness.repo.actions == {}
    assert harness.audit.entries == []


@pytest.mark.parametrize("role", [Role.caseworker, Role.supervisor])
def test_FR_001_07_allowed_action_signals_with_principal_reviewer_returns_202(
    harness: CaseApiHarness, role: Role
) -> None:
    case = put_case(harness)
    harness.workflows.allowed = AllowedActions(task_id=TASK, actions=BOTH)
    user = harness.sign_in(role)
    body = action_body()

    spoofed = harness.client.post(
        actions_url(case.case_id), json={**body, "reviewer_id": "cw-synth-1"}
    )
    response = harness.client.post(actions_url(case.case_id), json=body)

    assert spoofed.status_code == 422  # a reviewer in the body is never accepted
    assert response.status_code == 202
    [(case_id, request)] = harness.workflows.signals
    assert case_id == case.case_id
    assert request.task_id == TASK
    assert request.action is ActionType.approve_denial
    assert request.reviewer_id == user.id
    assert request.reviewer_role is role
    assert str(request.action_id) == body["request_id"]  # 010's signal payload (ADR-001-11)


def test_FR_001_20_review_action_logs_no_reason_text(
    harness: CaseApiHarness, caplog: pytest.LogCaptureFixture
) -> None:
    stream = io.StringIO()
    configure_logging(harness.settings, "api", stream=stream)
    case = put_case(harness)
    harness.workflows.allowed = AllowedActions(task_id=TASK, actions=BOTH)
    harness.sign_in(Role.caseworker)

    accepted = harness.client.post(
        actions_url(case.case_id), json=action_body("override_to_approve", SYNTH_REASON)
    )
    harness.workflows.allowed = AllowedActions()
    refused = harness.client.post(
        actions_url(case.case_id), json=action_body("override_to_approve", SYNTH_REASON)
    )

    assert (accepted.status_code, refused.status_code) == (202, 409)
    assert harness.workflows.signals[0][1].reason == SYNTH_REASON  # reaches the workflow
    logged = stream.getvalue()
    assert "case.action_signalled" in logged and "case.action_refused" in logged
    assert str(case.case_id) in logged  # PL-11: case_id is bound on every line
    for text in (logged, caplog.text, refused.text):
        assert SYNTH_REASON not in text
        assert str(HOUSEHOLD) not in text


def test_FR_001_11_get_case_returns_days_remaining_and_at_risk(harness: CaseApiHarness) -> None:
    case = put_case(harness, age_days=36)
    harness.repo.tasks[case.case_id] = OpenReviewTask(
        task_id=TASK, reason=ReviewReason.adverse_determination, allowed_actions=BOTH
    )
    young = put_case(harness, age_days=3, household=uuid.uuid4(), state=CaseState.determining)
    decided = put_case(harness, age_days=40, household=uuid.uuid4(), state=CaseState.decided)

    harness.sign_in(Role.caseworker)
    body = harness.client.get(f"/api/v1/cases/{case.case_id}").json()
    young_body = harness.client.get(f"/api/v1/cases/{young.case_id}").json()
    decided_body = harness.client.get(f"/api/v1/cases/{decided.case_id}").json()
    harness.sign_in(Role.applicant)
    own = harness.client.get(f"/api/v1/cases/{case.case_id}")

    assert body["days_remaining"] == 9
    assert body["at_risk"] is True
    assert body["state"] == "under_review"
    assert body["decision_deadline"] == (case.received_on + timedelta(days=45)).isoformat()
    assert body["open_review_task"] == {
        "task_id": str(TASK),
        "reason": "adverse_determination",
        "allowed_actions": ["approve_denial", "override_to_approve"],
    }
    assert (young_body["days_remaining"], young_body["at_risk"]) == (42, False)
    assert young_body["open_review_task"] is None
    assert (decided_body["days_remaining"], decided_body["at_risk"]) == (None, False)
    assert own.status_code == 200
    assert date.fromisoformat(own.json()["received_on"]) == case.received_on


def test_FR_001_11_get_case_of_other_household_returns_404(harness: CaseApiHarness) -> None:
    other = put_case(harness, household=uuid.uuid4())

    harness.sign_in(Role.applicant)
    foreign = harness.client.get(f"/api/v1/cases/{other.case_id}")
    missing = harness.client.get(f"/api/v1/cases/{uuid.uuid4()}")
    harness.sign_out()
    anonymous = harness.client.get(f"/api/v1/cases/{other.case_id}")

    assert foreign.status_code == 404
    assert foreign.json()["error"]["code"] == "not_found"
    # Another household's case looks exactly like a case that does not exist.
    assert missing.status_code == 404
    assert foreign.json()["error"]["message"] == missing.json()["error"]["message"]
    assert anonymous.status_code == 401


def test_FR_001_27_case_detail_is_a_superset_of_case_status(harness: CaseApiHarness) -> None:
    case = put_case(harness, age_days=3)

    harness.sign_in(Role.supervisor)
    body = harness.client.get(f"/api/v1/cases/{case.case_id}").json()

    assert body["case_type"] == "application"
    assert body["coverage_period"] is None and body["override"] is None
    assert body["demo_clock"] is False
    assert body["days_remaining"] == 42


def test_FR_001_26_list_cases_requires_sign_in(harness: CaseApiHarness) -> None:
    put_case(harness)

    anonymous = harness.client.get("/api/v1/cases")
    seen = {}
    for role in (Role.applicant, Role.caseworker, Role.supervisor):
        harness.sign_in(role)
        seen[role] = harness.client.get("/api/v1/cases")

    assert anonymous.status_code == 401
    assert {r: resp.status_code for r, resp in seen.items()} == {
        Role.applicant: 200,
        Role.caseworker: 200,
        Role.supervisor: 200,
    }
    assert len(seen[Role.applicant].json()["items"]) == 1  # HOUSEHOLD's own case
    assert seen[Role.caseworker].json()["next_cursor"] is None


@pytest.mark.parametrize(
    "query", ["cursor=!!", f"cursor={'x' * 201}", "limit=0", "limit=101", "state=nope"]
)
def test_FR_001_27_invalid_cursor_or_limit_returns_422(harness: CaseApiHarness, query: str) -> None:
    harness.sign_in(Role.caseworker)

    response = harness.client.get(f"/api/v1/cases?{query}")

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    assert error["fields"]


def test_FR_001_27_list_filters_by_state_and_pages(harness: CaseApiHarness) -> None:
    for n in range(3):
        put_case(harness, household=uuid.uuid4(), age_days=n)
    decided = put_case(harness, household=uuid.uuid4(), state=CaseState.decided)
    harness.sign_in(Role.caseworker)

    first = harness.client.get("/api/v1/cases?limit=2").json()
    second = harness.client.get(f"/api/v1/cases?limit=2&cursor={first['next_cursor']}").json()
    by_state = harness.client.get("/api/v1/cases?state=decided").json()

    assert len(first["items"]) == 2 and first["next_cursor"]
    assert len(second["items"]) == 1 and second["next_cursor"] is None
    assert [i["case_id"] for i in by_state["items"]] == [str(decided.case_id)]


def _events(harness: CaseApiHarness, case: Case, count: int) -> None:
    for seq in range(1, count + 1):
        harness.repo.events.append(
            CaseEvent(
                id=uuid.uuid4(),
                case_id=case.case_id,
                household_id=case.household_id,
                seq=seq,
                kind=CaseEventKind.state_changed,
                case_time=FIXED_NOW,
                real_time=FIXED_NOW,
                actor_type=EventActorType.system,
                reason_code="verification_started",
                created_at=FIXED_NOW,
            )
        )


def test_FR_001_27_history_rejects_applicant_role(harness: CaseApiHarness) -> None:
    case = put_case(harness)
    _events(harness, case, 1)
    url = f"/api/v1/cases/{case.case_id}/history"

    harness.sign_in(Role.applicant)
    applicant = harness.client.get(url)
    harness.sign_out()
    anonymous = harness.client.get(url)
    codes = {}
    for role in (Role.caseworker, Role.supervisor):
        harness.sign_in(role)
        codes[role] = harness.client.get(url).status_code

    assert applicant.status_code == 403
    assert applicant.json()["error"]["code"] == "auth.forbidden"
    assert anonymous.status_code == 401
    assert codes == {Role.caseworker: 200, Role.supervisor: 200}


def test_FR_001_27_history_of_missing_case_returns_404(harness: CaseApiHarness) -> None:
    harness.sign_in(Role.caseworker)

    response = harness.client.get(f"/api/v1/cases/{uuid.uuid4()}/history")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_FR_001_27_history_pages_by_seq_cursor(harness: CaseApiHarness) -> None:
    case = put_case(harness)
    _events(harness, case, 120)
    harness.sign_in(Role.caseworker)
    url = f"/api/v1/cases/{case.case_id}/history"

    first = harness.client.get(f"{url}?limit=50").json()
    second = harness.client.get(f"{url}?limit=50&cursor={first['next_cursor']}").json()
    third = harness.client.get(f"{url}?limit=50&cursor={second['next_cursor']}").json()

    assert (first["next_cursor"], second["next_cursor"], third["next_cursor"]) == (50, 100, None)
    assert [len(p["items"]) for p in (first, second, third)] == [50, 50, 20]
    assert first["items"][0]["actor"] == "System"
    assert harness.client.get(f"{url}?limit=0").status_code == 422
    assert harness.client.get(f"{url}?limit=101").status_code == 422


def test_FR_001_26_applicant_miss_is_audited_through_the_api(harness: CaseApiHarness) -> None:
    other = put_case(harness, household=uuid.uuid4())
    harness.sign_in(Role.applicant)

    foreign = harness.client.get(f"/api/v1/cases/{other.case_id}")
    missing = harness.client.get(f"/api/v1/cases/{uuid.uuid4()}")

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["error"]["message"] == missing.json()["error"]["message"]
    wrong_case = [e for e in harness.audit.entries if e.target_type == "case"]
    assert len(wrong_case) == 2
    assert str(other.case_id) in {e.target_id for e in wrong_case}
