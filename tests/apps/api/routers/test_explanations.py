"""GET /cases/{id}/explanations and GET /me/history (TC-23 to TC-37; FR-004-15, FR-004-16)."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from uuid import UUID

import pytest

from apps.api.dependencies.cases import get_explanation_repository
from apps.api.repositories.explanations import MAX_EXPLANATIONS, MAX_HISTORY, HistoryRow
from packages.domain.audit import AuditAction, AuditOutcome
from packages.domain.auth import Role
from packages.domain.case import (
    Case,
    CaseOutcome,
    CaseState,
    CoveragePeriod,
    CoverageStatus,
    DeterminationOutcome,
)
from packages.domain.eligibility import RuleId
from packages.domain.explanation import Citation, DecisionExplanation, ExplanationAudience
from packages.platform.scope import Scope
from packages.platform.settings import Settings
from tests.apps.api.case_fakes import FIXED_NOW, CaseApiHarness
from tests.apps.api.fakes import HOUSEHOLD
from tests.apps.api.routers.test_cases import put_case

OTHER_HOUSEHOLD = uuid.uuid4()
CASEWORKER_TEXT = "Caseworker text: income 1000.00 is under the limit 2500.00 (ELIG-06)."
APPLICANT_TEXT = "Your household income is under the limit."


class FakeExplanationRepository:
    """Applies the same scope, order and caps as the Mongo repository."""

    def __init__(self, harness: CaseApiHarness) -> None:
        self.harness = harness
        self.explanations: list[DecisionExplanation] = []
        self.rows: dict[UUID, HistoryRow] = {}
        self.calls: list[tuple[str, Scope]] = []

    async def list_for_case(self, scope: Scope, case_id: UUID) -> list[DecisionExplanation]:
        self.calls.append(("list_for_case", scope))
        rows = [
            e
            for e in self.explanations
            if e.case_id == case_id and e.audience is ExplanationAudience.caseworker
        ]
        return sorted(rows, key=lambda e: e.built_at, reverse=True)[:MAX_EXPLANATIONS]

    async def history(self, scope: Scope, household_id: UUID) -> list[HistoryRow]:
        self.calls.append(("history", scope))
        rows = [r for r in self.rows.values() if r.case.household_id == household_id]
        rows.sort(key=lambda r: r.case.received_on, reverse=True)
        return rows[:MAX_HISTORY]


@pytest.fixture
def harness(settings: Settings) -> CaseApiHarness:
    return CaseApiHarness(settings)


@pytest.fixture
def store(harness: CaseApiHarness) -> FakeExplanationRepository:
    repo = FakeExplanationRepository(harness)
    harness.client.app.dependency_overrides[get_explanation_repository] = (  # type: ignore[attr-defined]
        lambda: repo
    )
    return repo


def explanation(
    case: Case,
    *,
    audience: ExplanationAudience = ExplanationAudience.caseworker,
    minutes: int = 0,
    text: str | None = None,
    result: DeterminationOutcome = DeterminationOutcome.eligible,
) -> DecisionExplanation:
    return DecisionExplanation(
        determination_id=uuid.uuid4(),
        case_id=case.case_id,
        household_id=case.household_id,
        audience=audience,
        result=result,
        text=text
        or (CASEWORKER_TEXT if audience is ExplanationAudience.caseworker else APPLICANT_TEXT),
        citations=[
            Citation(
                rule_id=RuleId.ELIG_06,
                section="TM-3.1",
                title="Income limit",
                manual_version=date(2026, 1, 1),
            )
        ],
        built_at=FIXED_NOW + timedelta(minutes=minutes),
    )


def url(case_id: object) -> str:
    return f"/api/v1/cases/{case_id}/explanations"


def decided_case(
    harness: CaseApiHarness,
    store: FakeExplanationRepository,
    *,
    household: UUID = HOUSEHOLD,
    received: date | None = None,
    outcome: CaseOutcome | None = CaseOutcome.approved,
    explained: bool = True,
) -> Case:
    case = put_case(harness, household=household, state=CaseState.decided)
    if received is not None:
        case = case.model_copy(update={"received_on": received})
        harness.repo.cases[case.case_id] = case
    day = case.received_on
    case = case.model_copy(update={"outcome": outcome, "decided_on": day + timedelta(days=5)})
    harness.repo.cases[case.case_id] = case
    coverage = CoveragePeriod(
        id=uuid.uuid4(),
        household_id=household,
        case_id=case.case_id,
        start_date=day + timedelta(days=5),
        end_date=day + timedelta(days=370),
        status=CoverageStatus.active,
    )
    applicant = explanation(case, audience=ExplanationAudience.applicant) if explained else None
    store.rows[case.case_id] = HistoryRow(case=case, coverage=coverage, explanation=applicant)
    return case


def test_FR_004_15_01_caseworker_reads_stored_explanations_with_citations_newest_first(
    harness: CaseApiHarness, store: FakeExplanationRepository
) -> None:
    case = put_case(harness, state=CaseState.decided)
    older = explanation(case, minutes=0)
    newer = explanation(case, minutes=5)
    store.explanations = [older, newer]
    harness.sign_in(Role.caseworker)

    response = harness.client.get(url(case.case_id))

    assert response.status_code == 200
    items = response.json()["items"]
    assert [i["determination_id"] for i in items] == [
        str(newer.determination_id),
        str(older.determination_id),
    ]
    assert items[0]["citations"] == [
        {
            "rule_id": "ELIG-06",
            "section": "TM-3.1",
            "title": "Income limit",
            "manual_version": "2026-01-01",
        }
    ]


def test_FR_004_15_02_case_without_stored_explanation_returns_empty_items(
    harness: CaseApiHarness, store: FakeExplanationRepository
) -> None:
    case = put_case(harness, state=CaseState.verifying)
    harness.sign_in(Role.caseworker)

    response = harness.client.get(url(case.case_id))

    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_FR_004_15_03_list_returns_only_caseworker_audience_text_as_stored(
    harness: CaseApiHarness, store: FakeExplanationRepository
) -> None:
    case = put_case(harness, state=CaseState.decided)
    store.explanations = [
        explanation(case, audience=ExplanationAudience.applicant, minutes=9),
        explanation(case),
    ]
    harness.sign_in(Role.caseworker)

    items = harness.client.get(url(case.case_id)).json()["items"]

    assert [(i["audience"], i["text"]) for i in items] == [("caseworker", CASEWORKER_TEXT)]


def test_FR_004_15_09_list_is_capped_at_20_items(
    harness: CaseApiHarness, store: FakeExplanationRepository
) -> None:
    case = put_case(harness, state=CaseState.decided)
    store.explanations = [explanation(case, minutes=m) for m in range(21)]
    harness.sign_in(Role.caseworker)

    items = harness.client.get(url(case.case_id)).json()["items"]

    assert len(items) == 20
    assert items[0]["built_at"] == (FIXED_NOW + timedelta(minutes=20)).isoformat().replace(
        "+00:00", "Z"
    )


def test_FR_004_15_04_successful_read_writes_one_determinations_read_audit_entry_without_personal_data(  # noqa: E501
    harness: CaseApiHarness, store: FakeExplanationRepository
) -> None:
    case = put_case(harness, state=CaseState.decided)
    store.explanations = [explanation(case)]
    user = harness.sign_in(Role.caseworker)

    harness.client.get(url(case.case_id))

    [entry] = harness.audit.entries
    assert entry.action is AuditAction.determinations_read
    assert entry.outcome is AuditOutcome.success
    assert entry.target_type == "case_explanations"
    assert entry.target_id == str(case.case_id)
    assert entry.actor_id == str(user.id)
    assert entry.occurred_at == FIXED_NOW
    assert CASEWORKER_TEXT not in entry.model_dump_json()


def test_FR_004_15_05_case_outside_caller_scope_returns_404_not_found_and_no_success_audit(
    harness: CaseApiHarness, store: FakeExplanationRepository
) -> None:
    harness.sign_in(Role.caseworker)

    response = harness.client.get(url(uuid.uuid4()))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert harness.audit.entries == []
    assert store.calls == []


def test_FR_004_15_06_applicant_gets_403_on_case_explanations(
    harness: CaseApiHarness, store: FakeExplanationRepository
) -> None:
    case = put_case(harness, household=HOUSEHOLD, state=CaseState.decided)
    store.explanations = [explanation(case)]
    harness.sign_in(Role.applicant)

    response = harness.client.get(url(case.case_id))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "auth.forbidden"
    assert store.calls == []


def test_FR_004_15_07_supervisor_can_read_case_explanations(
    harness: CaseApiHarness, store: FakeExplanationRepository
) -> None:
    case = put_case(harness, state=CaseState.decided)
    store.explanations = [explanation(case)]
    harness.sign_in(Role.supervisor)

    response = harness.client.get(url(case.case_id))

    assert response.status_code == 200
    assert len(response.json()["items"]) == 1


def test_FR_004_15_08_unauthenticated_request_returns_401_error_body(
    harness: CaseApiHarness, store: FakeExplanationRepository
) -> None:
    case = put_case(harness, state=CaseState.decided)

    response = harness.client.get(url(case.case_id))

    assert response.status_code == 401
    assert "error" in response.json()
    assert store.calls == []


def test_FR_004_16_02_history_lists_each_coverage_period_with_applicant_explanation_as_recorded(
    harness: CaseApiHarness, store: FakeExplanationRepository
) -> None:
    first = decided_case(harness, store, received=date(2026, 1, 5))
    second = decided_case(harness, store, received=date(2026, 2, 9), outcome=CaseOutcome.denied)
    harness.sign_in(Role.applicant)

    response = harness.client.get("/api/v1/me/history")

    assert response.status_code == 200
    items = response.json()["items"]
    assert [i["case_id"] for i in items] == [str(second.case_id), str(first.case_id)]
    assert items[0]["outcome"] == "denied"
    assert items[1]["outcome"] == "approved"
    assert items[1]["coverage_start"] == "2026-01-10"
    assert items[1]["coverage_end"] == "2027-01-10"
    assert items[1]["decided_on"] == "2026-01-10"
    assert items[1]["explanation"]["text"] == APPLICANT_TEXT
    assert items[1]["explanation"]["audience"] == "applicant"


def test_FR_004_16_03_history_orders_newest_received_first_and_caps_at_50(
    harness: CaseApiHarness, store: FakeExplanationRepository
) -> None:
    base = date(2025, 1, 1)
    cases = [decided_case(harness, store, received=base + timedelta(days=i)) for i in range(51)]
    harness.sign_in(Role.applicant)

    items = harness.client.get("/api/v1/me/history").json()["items"]

    assert len(items) == 50
    assert items[0]["case_id"] == str(cases[-1].case_id)
    assert items[-1]["case_id"] == str(cases[1].case_id)


def test_FR_004_16_04_history_excludes_other_households_cases(
    harness: CaseApiHarness, store: FakeExplanationRepository
) -> None:
    mine = decided_case(harness, store)
    decided_case(harness, store, household=OTHER_HOUSEHOLD)
    harness.sign_in(Role.applicant)

    items = harness.client.get("/api/v1/me/history").json()["items"]

    assert [i["case_id"] for i in items] == [str(mine.case_id)]
    scopes = [scope for name, scope in store.calls if name == "history"]
    assert [s.household_id for s in scopes] == [HOUSEHOLD]


@pytest.mark.parametrize("role", [Role.caseworker, Role.supervisor])
def test_FR_004_16_05_caseworker_and_supervisor_get_403_on_history(
    harness: CaseApiHarness, store: FakeExplanationRepository, role: Role
) -> None:
    decided_case(harness, store)
    harness.sign_in(role)

    response = harness.client.get("/api/v1/me/history")

    assert response.status_code == 403
    assert store.calls == []


def test_FR_004_16_06_case_without_determination_has_null_explanation(
    harness: CaseApiHarness, store: FakeExplanationRepository
) -> None:
    case = decided_case(harness, store, outcome=None, explained=False)
    harness.sign_in(Role.applicant)

    [item] = harness.client.get("/api/v1/me/history").json()["items"]

    assert item["case_id"] == str(case.case_id)
    assert item["outcome"] is None
    assert item["explanation"] is None


def test_FR_004_16_07_overridden_case_history_shows_stored_explanation_unchanged(
    harness: CaseApiHarness, store: FakeExplanationRepository
) -> None:
    case = decided_case(harness, store, outcome=CaseOutcome.approved, explained=False)
    stored = explanation(
        case,
        audience=ExplanationAudience.applicant,
        result=DeterminationOutcome.ineligible,
        text="Your household income is over the limit.",
    )
    store.rows[case.case_id] = HistoryRow(
        case=case, coverage=store.rows[case.case_id].coverage, explanation=stored
    )
    harness.sign_in(Role.applicant)

    [item] = harness.client.get("/api/v1/me/history").json()["items"]

    assert item["outcome"] == "approved"
    assert item["explanation"]["result"] == "ineligible"
    assert item["explanation"]["text"] == "Your household income is over the limit."
    assert harness.audit.entries == []
