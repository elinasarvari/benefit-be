"""correct_value and dismiss_finding on the action route (TC-53 to TC-56; FR-002-19, PL-4, PL-7)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from packages.domain.auth import Role
from packages.domain.case import ActionType, AllowedActions, CaseState
from packages.platform.settings import Settings
from tests.apps.api.case_fakes import CaseApiHarness
from tests.apps.api.routers.test_cases import TASK, actions_url, put_case

REASON = "Caseworker checked the stub with the applicant."
FINDING = str(uuid.uuid4())
CORRECTED = {"field_ref": f"pay_stub:{uuid.uuid4()}:gross_pay", "value": "350.00"}


@pytest.fixture
def harness(settings: Settings) -> CaseApiHarness:
    h = CaseApiHarness(settings)
    h.workflows.allowed = AllowedActions(
        task_id=TASK,
        actions=[ActionType.correct_value, ActionType.dismiss_finding, ActionType.accept_findings],
    )
    return h


def correct(**overrides: Any) -> dict[str, Any]:
    return {
        "action": "correct_value",
        "request_id": str(uuid.uuid4()),
        "reason": REASON,
        "corrected_value": CORRECTED,
        **overrides,
    }


def dismiss(**overrides: Any) -> dict[str, Any]:
    return {
        "action": "dismiss_finding",
        "request_id": str(uuid.uuid4()),
        "reason": REASON,
        "finding_id": FINDING,
        **overrides,
    }


def test_FR_002_19_correct_and_dismiss_are_signalled_with_their_fields(
    harness: CaseApiHarness,
) -> None:
    case = put_case(harness)
    caseworker = harness.sign_in(Role.caseworker)

    first = harness.client.post(actions_url(case.case_id), json=correct())
    second = harness.client.post(actions_url(case.case_id), json=dismiss())

    assert (first.status_code, second.status_code) == (202, 202)
    [(_, corrected), (_, dismissed)] = harness.workflows.signals
    assert corrected.action is ActionType.correct_value
    assert corrected.corrected_value is not None
    assert corrected.corrected_value.field_ref == CORRECTED["field_ref"]
    assert corrected.reason == REASON
    assert corrected.reviewer_id == caseworker.id  # from the session
    assert dismissed.action is ActionType.dismiss_finding
    assert str(dismissed.finding_id) == FINDING


def test_FR_002_19_reason_required(harness: CaseApiHarness) -> None:
    case = put_case(harness)
    harness.sign_in(Role.caseworker)
    bodies = [
        correct(reason=None),
        correct(reason=""),
        correct(reason="   "),
        dismiss(reason=None),
        dismiss(reason=""),
        dismiss(reason="  \n"),
    ]
    missing_reason = correct()
    del missing_reason["reason"]
    bodies += [missing_reason]

    for body in bodies:
        response = harness.client.post(actions_url(case.case_id), json=body)
        assert response.status_code == 422, body
        assert REASON not in response.text

    assert harness.workflows.signals == []


def test_FR_002_19_each_action_needs_its_own_field(harness: CaseApiHarness) -> None:
    case = put_case(harness)
    harness.sign_in(Role.caseworker)
    no_value = correct()
    del no_value["corrected_value"]
    no_finding = dismiss()
    del no_finding["finding_id"]

    assert harness.client.post(actions_url(case.case_id), json=no_value).status_code == 422
    assert harness.client.post(actions_url(case.case_id), json=no_finding).status_code == 422
    assert harness.workflows.signals == []


def test_FR_002_19_action_additive_existing_actions_unchanged(harness: CaseApiHarness) -> None:
    case = put_case(harness)
    harness.workflows.allowed = AllowedActions(
        task_id=TASK, actions=[ActionType.approve_denial, ActionType.override_to_approve]
    )
    harness.sign_in(Role.caseworker)

    approve = {"action": "approve_denial", "request_id": str(uuid.uuid4())}
    override = {
        "action": "override_to_approve",
        "request_id": str(uuid.uuid4()),
        "reason": "Income was documented.",
    }

    assert harness.client.post(actions_url(case.case_id), json=approve).status_code == 202
    assert harness.client.post(actions_url(case.case_id), json=override).status_code == 202
    blank_override = {**override, "reason": " ", "request_id": str(uuid.uuid4())}
    assert harness.client.post(actions_url(case.case_id), json=blank_override).status_code == 422


def test_FR_002_19_action_route_refused_for_every_unauthorised_role(
    harness: CaseApiHarness,
) -> None:
    case = put_case(harness)

    harness.sign_in(Role.applicant)
    for body in (correct(), dismiss()):
        assert harness.client.post(actions_url(case.case_id), json=body).status_code == 403
    harness.sign_out()
    for body in (correct(), dismiss()):
        assert harness.client.post(actions_url(case.case_id), json=body).status_code == 401

    assert harness.workflows.signals == []


def test_FR_002_19_action_on_decided_case_or_closed_task_409(harness: CaseApiHarness) -> None:
    decided = put_case(harness, state=CaseState.decided)
    open_case = put_case(harness)
    harness.sign_in(Role.caseworker)

    harness.workflows.allowed = AllowedActions()  # decided: no open task
    harness.workflows.completed = True
    closed = harness.client.post(actions_url(decided.case_id), json=correct())

    harness.workflows.completed = False
    harness.workflows.allowed = AllowedActions(
        task_id=uuid.uuid4(), actions=[ActionType.correct_value]
    )
    other_task = harness.client.post(actions_url(open_case.case_id), json=dismiss())

    for response in (closed, other_task):
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"
    applied = [s for s in harness.workflows.signals if s[1].action is ActionType.correct_value]
    assert applied == []
    assert list(harness.repo.actions.values())[0].outcome.value == "rejected"
