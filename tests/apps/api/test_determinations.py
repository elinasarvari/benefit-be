"""GET /api/v1/cases/{case_id}/determinations (TC-103 to TC-113; FR-003-23, FR-003-05; PL-7)."""

from __future__ import annotations

import importlib
import uuid
from datetime import date, timedelta
from typing import Any
from uuid import UUID

import pytest

from apps.api.dependencies.cases import get_determination_repository
from packages.domain.audit import AuditAction, AuditOutcome, ReasonCode
from packages.domain.auth import Role
from packages.domain.case import CaseState
from packages.domain.eligibility import DeterminationRecord
from packages.domain.household import HouseholdSnapshot
from packages.platform.scope import Scope
from packages.platform.settings import Settings
from packages.rules.engine import evaluate
from tests.apps.api.case_fakes import FIXED_NOW, CaseApiHarness
from tests.apps.api.fakes import HOUSEHOLD
from tests.apps.api.routers.test_cases import put_case
from tests.packages.rules.conftest import AS_OF, job, member, snapshot, stub

SYNTHETIC_NAME = "Sample Synthetic Person"


class FakeDeterminationRepository:
    def __init__(self) -> None:
        self.records: list[DeterminationRecord] = []
        self.calls: list[tuple[str, Scope]] = []

    async def list_for_case(self, scope: Scope, case_id: UUID) -> list[DeterminationRecord]:
        self.calls.append(("list_for_case", scope))
        rows = [r for r in self.records if r.case_id == case_id]
        return sorted(rows, key=lambda r: r.recorded_at, reverse=True)[:20]


@pytest.fixture
def harness(settings: Settings) -> CaseApiHarness:
    return CaseApiHarness(settings)


@pytest.fixture
def determinations(harness: CaseApiHarness) -> FakeDeterminationRepository:
    repo = FakeDeterminationRepository()
    harness.client.app.dependency_overrides[get_determination_repository] = (  # type: ignore[attr-defined]
        lambda: repo
    )
    return repo


def url(case_id: object) -> str:
    return f"/api/v1/cases/{case_id}/determinations"


def record(
    case_id: UUID,
    snap: HouseholdSnapshot,
    *,
    minutes: int = 0,
    as_of: date = AS_OF,
) -> DeterminationRecord:
    return DeterminationRecord(
        determination_id=uuid.uuid4(),
        household_id=HOUSEHOLD,
        case_id=case_id,
        snapshot_id=snap.snapshot_id,
        recorded_at=FIXED_NOW + timedelta(minutes=minutes),
        determination=evaluate(snap, as_of),
    )


def over_limit() -> HouseholdSnapshot:
    return snapshot([member(jobs=[job("rich", [stub("weekly", "2000")])])])


def no_stub() -> HouseholdSnapshot:
    return snapshot([member(jobs=[job("nostub", [])])])


def test_FR_003_23_caseworker_gets_determinations_newest_first_with_reason_code(
    harness: CaseApiHarness, determinations: FakeDeterminationRepository
) -> None:
    case = put_case(harness, state=CaseState.decided)
    older = record(case.case_id, over_limit(), minutes=0)
    newer = record(case.case_id, snapshot([member()]), minutes=5)
    determinations.records = [older, newer]
    harness.sign_in(Role.caseworker)

    response = harness.client.get(url(case.case_id))

    assert response.status_code == 200
    items = response.json()["items"]
    assert [i["determination_id"] for i in items] == [
        str(newer.determination_id),
        str(older.determination_id),
    ]
    assert [(i["result"], i["reason_code"]) for i in items] == [
        ("eligible", None),
        ("ineligible", "income_over_limit"),
    ]
    assert all(len(i["rules"]) == 6 for i in items)
    assert items[1]["as_of_date"] == AS_OF.isoformat()
    assert items[1]["rule_set_id"] and items[1]["rule_set_version"] == 1


def test_FR_003_23_supervisor_can_read_case_determinations(
    harness: CaseApiHarness, determinations: FakeDeterminationRepository
) -> None:
    case = put_case(harness)
    determinations.records = [record(case.case_id, snapshot([member()]))]
    harness.sign_in(Role.supervisor)

    response = harness.client.get(url(case.case_id))

    assert response.status_code == 200
    assert len(response.json()["items"]) == 1


def test_FR_003_23_applicant_gets_403_error_envelope(
    harness: CaseApiHarness, determinations: FakeDeterminationRepository
) -> None:
    case = put_case(harness, household=HOUSEHOLD)
    determinations.records = [record(case.case_id, snapshot([member()]))]
    harness.sign_in(Role.applicant)

    response = harness.client.get(url(case.case_id))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "auth.forbidden"
    assert determinations.calls == []


def test_FR_003_23_applicant_refusal_is_audited(
    harness: CaseApiHarness, determinations: FakeDeterminationRepository
) -> None:
    case = put_case(harness, household=HOUSEHOLD)
    harness.sign_in(Role.applicant)

    harness.client.get(url(case.case_id))

    [entry] = harness.audit.entries
    assert entry.action is AuditAction.access_refused
    assert entry.outcome is AuditOutcome.refused
    assert entry.reason_code is ReasonCode.role_not_allowed
    assert entry.target_id == "/cases/{case_id}/determinations"


def test_FR_003_23_unauthenticated_gets_401_error_envelope(
    harness: CaseApiHarness, determinations: FakeDeterminationRepository
) -> None:
    case = put_case(harness)

    response = harness.client.get(url(case.case_id))

    assert response.status_code == 401
    assert "error" in response.json()
    assert determinations.calls == []


def test_FR_003_23_unknown_case_gets_404_error_envelope(
    harness: CaseApiHarness, determinations: FakeDeterminationRepository
) -> None:
    harness.sign_in(Role.caseworker)

    response = harness.client.get(url(uuid.uuid4()))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert determinations.calls == []


def test_FR_003_23_non_uuid_case_id_gets_422_error_envelope(
    harness: CaseApiHarness, determinations: FakeDeterminationRepository
) -> None:
    harness.sign_in(Role.caseworker)

    response = harness.client.get(url("abc"))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_FR_003_23_case_without_determination_returns_empty_list(
    harness: CaseApiHarness, determinations: FakeDeterminationRepository
) -> None:
    case = put_case(harness, state=CaseState.verifying)
    harness.sign_in(Role.caseworker)

    response = harness.client.get(url(case.case_id))

    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_FR_003_23_cannot_determine_view_lists_missing_items_and_null_reason(
    harness: CaseApiHarness, determinations: FakeDeterminationRepository
) -> None:
    case = put_case(harness, state=CaseState.under_review)
    determinations.records = [record(case.case_id, no_stub())]
    harness.sign_in(Role.caseworker)

    [item] = harness.client.get(url(case.case_id)).json()["items"]

    assert item["result"] == "cannot_determine"
    assert item["reason_code"] is None
    assert [m["item"] for m in item["missing_items"]] == ["pay_stub"]
    assert item["missing_items"][0]["job_id"] is not None


def _strings(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return [value] if isinstance(value, str) else []


def test_FR_003_05_api_rule_inputs_carry_member_ids_never_names(
    harness: CaseApiHarness, determinations: FakeDeterminationRepository
) -> None:
    case = put_case(harness, state=CaseState.decided)
    # The stored members carry names; the engine's facts (and so the trace) never do.
    people = member(citizenship_attested=None)
    determinations.records = [record(case.case_id, snapshot([people]))]
    harness.sign_in(Role.caseworker)

    body = harness.client.get(url(case.case_id)).json()

    assert any(str(people.member_id) in s for s in _strings(body))
    assert SYNTHETIC_NAME not in " ".join(_strings(body))
    for name in ("given_name", "family_name", "full_name"):
        assert name not in str(body)


def test_FR_003_23_determination_read_writes_audit_entry(
    harness: CaseApiHarness, determinations: FakeDeterminationRepository
) -> None:
    case = put_case(harness)
    harness.sign_in(Role.caseworker)

    harness.client.get(url(case.case_id))

    [entry] = harness.audit.entries
    assert entry.action is AuditAction.determinations_read
    assert entry.outcome is AuditOutcome.success
    assert entry.target_type == "case"
    assert entry.target_id == str(case.case_id)
    assert entry.occurred_at == FIXED_NOW


def test_FR_003_23_every_audit_action_is_allowed_by_the_audit_log_validator() -> None:
    """The read audit uses a new action; the audit log's validator must list it (R11)."""
    baseline = importlib.import_module("db.migrations.versions.0001_baseline")
    eligibility = importlib.import_module("db.migrations.versions.0005_eligibility")

    allowed = {*baseline.AUDIT_ACTIONS, eligibility.DETERMINATIONS_READ}

    assert {a.value for a in AuditAction} == allowed
