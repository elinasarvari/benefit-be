"""Case services of the API (001, T019, T060–T065; TC-50–57, 97–101, 105–107, 109, 112, 113)
with a fake repository, workflows, user names and clock."""

from __future__ import annotations

import io
import json
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from typing import TypeVar
from uuid import UUID, uuid4

import pytest
import structlog
from pydantic import ValidationError

from apps.api.repositories.cases import CaseAlreadyExists
from apps.api.services import cases as service
from packages.domain.audit import AuditAction, AuditOutcome, ReasonCode
from packages.domain.auth import Role
from packages.domain.case import (
    ApplicationSubmission,
    Case,
    CaseClock,
    CaseEvent,
    CaseEventKind,
    CaseOutcome,
    CaseOverride,
    CaseState,
    CaseType,
    CoveragePeriod,
    CoverageStatus,
    EventActorType,
    SubmissionRefusal,
    case_id_for,
)
from packages.platform.audit import InMemoryAuditWriter
from packages.platform.clock import FixedClock, case_today
from packages.platform.errors import Conflict, DependencyUnavailable, InvalidInput, NotFound
from packages.platform.logging import clear_context, configure_logging
from packages.platform.scope import Scope
from tests.apps.api.case_fakes import (
    FIXED_NOW,
    FakeCaseRepository,
    FakeCaseWorkflows,
    FakeUserNames,
    principal,
)
from tests.apps.api.forms import SYNTH_EMPLOYER, SYNTH_NAME, form, submission

H1 = UUID("00000000-0000-4000-8000-0000000000a1")
SUB_A = UUID("00000000-0000-4000-8000-00000000000a")
SUB_B = UUID("00000000-0000-4000-8000-00000000000b")
T = TypeVar("T")


@pytest.fixture
def repo() -> FakeCaseRepository:
    return FakeCaseRepository()


@pytest.fixture
def workflows() -> FakeCaseWorkflows:
    return FakeCaseWorkflows()


async def submit(
    repo: FakeCaseRepository,
    workflows: FakeCaseWorkflows,
    reference: UUID = SUB_A,
    *,
    household: UUID = H1,
    demo_profile: bool = False,
) -> service.SubmissionResult:
    return await service.submit_application(
        submission(reference),
        principal(Role.applicant, household),
        repo=repo,
        workflows=workflows,
        clock=FixedClock(FIXED_NOW),
        demo_profile=demo_profile,
    )


def coverage(end_date: date, household: UUID = H1, case_id: UUID | None = None) -> CoveragePeriod:
    return CoveragePeriod(
        id=uuid4(),
        household_id=household,
        case_id=case_id or uuid4(),
        start_date=date(2025, 3, 1),
        end_date=end_date,
        status=CoverageStatus.active,
    )


def refusal_of(error: Conflict) -> str:
    return error.fields[0].type


async def test_FR_001_01_submit_opens_submitted_case_with_deadline_45_days(
    repo: FakeCaseRepository, workflows: FakeCaseWorkflows
) -> None:
    result = await submit(repo, workflows)

    case_id = case_id_for(SUB_A)
    assert result.created is True
    assert result.status.case_id == case_id
    assert result.status.state is CaseState.submitted
    assert result.status.received_on == date(2026, 3, 17)
    assert result.status.decision_deadline == date(2026, 5, 1)
    assert result.status.days_remaining == 45
    assert result.status.at_risk is False
    stored = repo.cases[case_id]
    assert stored.open is True
    assert stored.state is CaseState.submitted
    assert stored.submission_reference == str(SUB_A)
    start = workflows.started[f"case-{case_id}"]
    assert start.case_id == case_id
    assert start.household_id == H1
    assert start.clock == stored.clock


async def test_FR_001_23_same_submission_twice_returns_existing_case(
    repo: FakeCaseRepository, workflows: FakeCaseWorkflows
) -> None:
    first = await submit(repo, workflows)
    second = await submit(repo, workflows)

    assert (first.created, second.created) == (True, False)
    assert second.status == first.status
    assert repo.inserts == 1
    assert len(workflows.started) == 1


async def test_FR_001_23_household_with_open_case_refused_with_reason(
    repo: FakeCaseRepository, workflows: FakeCaseWorkflows
) -> None:
    await submit(repo, workflows, SUB_A)

    with pytest.raises(Conflict) as refused:
        await submit(repo, workflows, SUB_B)

    assert refusal_of(refused.value) == SubmissionRefusal.household_has_open_case.value
    assert case_id_for(SUB_B) not in repo.cases
    assert f"case-{case_id_for(SUB_B)}" not in workflows.started


async def test_FR_001_23_household_with_unended_coverage_refused(
    repo: FakeCaseRepository, workflows: FakeCaseWorkflows
) -> None:
    repo.coverage.append(coverage(date(2026, 3, 17)))

    with pytest.raises(Conflict) as refused:
        await submit(repo, workflows)

    assert refusal_of(refused.value) == SubmissionRefusal.household_has_coverage.value
    assert repo.cases == {}
    assert workflows.start_calls == 0


async def test_FR_001_23_coverage_ended_yesterday_allows_new_application(
    repo: FakeCaseRepository, workflows: FakeCaseWorkflows
) -> None:
    repo.coverage.append(coverage(date(2026, 3, 16)))

    result = await submit(repo, workflows)

    assert result.created is True


async def test_FR_001_01_temporal_down_returns_503_and_retry_starts_workflow(
    repo: FakeCaseRepository, workflows: FakeCaseWorkflows
) -> None:
    workflows.fail_next_start = True

    with pytest.raises(DependencyUnavailable):
        await submit(repo, workflows)
    retried = await submit(repo, workflows)

    assert retried.created is False
    assert retried.status.case_id == case_id_for(SUB_A)
    assert repo.inserts == 1
    assert workflows.start_calls == 2
    assert f"case-{case_id_for(SUB_A)}" in workflows.started


@pytest.mark.parametrize(("demo_profile", "scale"), [(True, 1440), (False, 1)])
async def test_FR_001_13_demo_profile_case_clock_scale_1440(
    repo: FakeCaseRepository, workflows: FakeCaseWorkflows, demo_profile: bool, scale: int
) -> None:
    await submit(repo, workflows, demo_profile=demo_profile)

    clock = repo.cases[case_id_for(SUB_A)].clock
    assert clock.scale == scale
    assert clock.offset_seconds == 0
    assert clock.real_start == FIXED_NOW
    assert clock.case_start == FIXED_NOW


async def test_FR_001_23_household_taken_from_principal_not_body(
    repo: FakeCaseRepository, workflows: FakeCaseWorkflows
) -> None:
    foreign = uuid4()
    with pytest.raises(ValidationError):
        ApplicationSubmission.model_validate(
            {
                "submission_reference": str(SUB_A),
                "form": form().model_dump(mode="json"),
                "household_id": str(foreign),
            }
        )

    await submit(repo, workflows, household=H1)

    stored = repo.cases[case_id_for(SUB_A)]
    assert stored.household_id == H1
    assert workflows.started[f"case-{stored.case_id}"].household_id == H1


class RacingCaseRepository(FakeCaseRepository):
    """A concurrent request with the same reference inserts first (the unique `_id` wins)."""

    def __init__(self, winner_household: UUID) -> None:
        super().__init__()
        self.winner_household = winner_household
        self.winner: Case | None = None

    async def insert_case(self, scope: Scope, case: Case) -> None:
        self.winner = case.model_copy(update={"household_id": self.winner_household})
        raise CaseAlreadyExists()

    async def run_in_transaction(self, work: Callable[[FakeCaseRepository], Awaitable[T]]) -> T:
        try:
            return await super().run_in_transaction(work)
        finally:
            if self.winner is not None:  # the winner's commit survives our rollback
                self.cases[self.winner.id] = self.winner


async def test_FR_001_23_concurrent_same_reference_returns_winning_case(
    workflows: FakeCaseWorkflows,
) -> None:
    repo = RacingCaseRepository(winner_household=H1)

    result = await submit(repo, workflows)

    assert result.created is False
    assert result.status.case_id == case_id_for(SUB_A)
    assert repo.cases[case_id_for(SUB_A)].household_id == H1
    assert workflows.start_calls == 1
    assert f"case-{case_id_for(SUB_A)}" in workflows.started


async def test_FR_001_23_reference_of_another_household_refused_with_reason(
    repo: FakeCaseRepository, workflows: FakeCaseWorkflows
) -> None:
    other_household = UUID("00000000-0000-4000-8000-0000000000a2")
    await submit(repo, workflows, SUB_A, household=other_household)
    started = workflows.start_calls

    with pytest.raises(Conflict) as refused:
        await submit(repo, workflows, SUB_A, household=H1)

    assert refusal_of(refused.value) == SubmissionRefusal.submission_reference_in_use.value
    assert refused.value.fields[0].field == "submission_reference"
    assert repo.cases[case_id_for(SUB_A)].household_id == other_household  # never revealed
    assert workflows.start_calls == started


# --- Application form and related case (ADR-001-08, -09) ---------------------------------------


def related_of(error: Conflict) -> UUID | None:
    return error.related_case_id


async def test_FR_001_24_submit_writes_application_in_opening_transaction(
    repo: FakeCaseRepository, workflows: FakeCaseWorkflows
) -> None:
    await submit(repo, workflows)

    case_id = case_id_for(SUB_A)
    stored = repo.applications[case_id]
    assert repo.application_in_transaction == [True]  # same transaction as the case insert
    assert (stored.id, stored.case_id, stored.household_id) == (case_id, case_id, H1)
    assert stored.submission_reference == str(SUB_A)
    assert stored.form == form()
    assert stored.synthetic is True
    case = repo.cases[case_id]
    assert stored.submitted_at == case.clock.case_start == FIXED_NOW


async def test_FR_001_24_refused_or_repeated_submit_writes_no_application(
    repo: FakeCaseRepository, workflows: FakeCaseWorkflows
) -> None:
    h3 = UUID("00000000-0000-4000-8000-0000000000a3")
    repo.coverage.append(coverage(date(2026, 3, 20), household=h3))

    await submit(repo, workflows, SUB_A)
    await submit(repo, workflows, SUB_A)  # repeated
    with pytest.raises(Conflict):
        await submit(repo, workflows, SUB_B)  # open case
    with pytest.raises(Conflict):
        await submit(repo, workflows, UUID(int=3), household=h3)  # coverage

    assert repo.application_writes == 1
    assert list(repo.applications) == [case_id_for(SUB_A)]


async def test_FR_001_24_open_case_race_rolls_application_back_with_the_case(
    repo: FakeCaseRepository, workflows: FakeCaseWorkflows
) -> None:
    await submit(repo, workflows, SUB_A)
    other = UUID("00000000-0000-4000-8000-0000000000a2")
    # A second household's application is written, then its transaction fails: nothing remains.
    original = repo.insert_application

    async def failing(scope: Scope, application: object) -> None:
        await original(scope, application)  # type: ignore[arg-type]
        raise CaseAlreadyExists()

    repo.insert_application = failing  # type: ignore[method-assign]
    with pytest.raises(Conflict):
        await submit(repo, workflows, SUB_B, household=other)

    assert case_id_for(SUB_B) not in repo.applications
    assert case_id_for(SUB_B) not in repo.cases


async def test_FR_001_25_open_case_refusal_carries_open_case_id(
    repo: FakeCaseRepository, workflows: FakeCaseWorkflows
) -> None:
    await submit(repo, workflows, SUB_A)
    repo.cases[case_id_for(SUB_A)].state = CaseState.under_review

    with pytest.raises(Conflict) as refused:
        await submit(repo, workflows, SUB_B)

    assert refusal_of(refused.value) == "household_has_open_case"
    assert related_of(refused.value) == case_id_for(SUB_A)


async def test_FR_001_25_coverage_refusal_carries_case_that_started_coverage(
    repo: FakeCaseRepository, workflows: FakeCaseWorkflows
) -> None:
    c3 = uuid4()
    repo.coverage.append(coverage(date(2026, 3, 17) + timedelta(days=30), case_id=c3))

    with pytest.raises(Conflict) as refused:
        await submit(repo, workflows)

    assert refusal_of(refused.value) == "household_has_coverage"
    assert related_of(refused.value) == c3


async def test_FR_001_25_submission_reference_in_use_refusal_has_null_related_case(
    repo: FakeCaseRepository, workflows: FakeCaseWorkflows
) -> None:
    other = UUID("00000000-0000-4000-8000-0000000000a2")
    await submit(repo, workflows, SUB_A, household=other)

    with pytest.raises(Conflict) as refused:
        await submit(repo, workflows, SUB_A, household=H1)

    assert refusal_of(refused.value) == "submission_reference_in_use"
    assert related_of(refused.value) is None
    assert str(case_id_for(SUB_A)) not in refused.value.message  # the other case is not revealed


async def test_FR_001_24_submit_service_logs_no_form_fields(
    repo: FakeCaseRepository, workflows: FakeCaseWorkflows, settings: object
) -> None:
    stream = io.StringIO()
    configure_logging(settings, "api", stream=stream)  # type: ignore[arg-type]
    try:
        await submit(repo, workflows, SUB_A)
        with pytest.raises(Conflict):
            await submit(repo, workflows, SUB_B)
        structlog.get_logger().info("probe", members=[{"full_name": SYNTH_NAME}], form={})
    finally:
        clear_context()

    logged = stream.getvalue()
    assert "case.submitted" in logged and "case.submission_refused" in logged
    for text in (
        SYNTH_NAME,
        SYNTH_EMPLOYER,
        "SYNTH-SPOUSE",
        "1985-04-12",
        "Synthetic Way",
        "62701",
    ):
        assert text not in logged
    for line in logged.splitlines():
        record = json.loads(line)
        assert not {"employer", "relationship", "members", "form"} & {
            k for k, v in record.items() if v not in ("***", None)
        }


# --- Reads: list, detail, history --------------------------------------------------------------

H2 = UUID("00000000-0000-4000-8000-0000000000a2")
H4 = UUID("00000000-0000-4000-8000-0000000000a4")
CW1 = UUID("00000000-0000-4000-8000-0000000000c1")
CW2 = UUID("00000000-0000-4000-8000-0000000000c2")


def make_case(
    repo: FakeCaseRepository,
    *,
    age_days: int = 3,
    household: UUID = H1,
    state: CaseState = CaseState.under_review,
    scale: int = 1,
    case_id: UUID | None = None,
    deadline_offset: int = 0,
) -> Case:
    real_start = (
        FIXED_NOW - timedelta(days=age_days)
        if scale == 1
        else FIXED_NOW - timedelta(minutes=age_days)
    )
    clock = CaseClock(real_start=real_start, case_start=real_start, scale=scale)
    received = case_today(clock, real_start)
    cid = case_id or uuid4()
    case = Case(
        id=cid,
        case_id=cid,
        household_id=household,
        case_type=CaseType.application,
        submission_reference=str(uuid4()),
        state=state,
        open=state is not CaseState.decided,
        received_on=received,
        decision_deadline=received + timedelta(days=45 + deadline_offset),
        clock=clock,
        created_at=real_start,
        updated_at=real_start,
    )
    repo.cases[cid] = case
    return case


async def test_FR_001_27_list_rows_compute_days_remaining_and_at_risk_per_case_clock(
    repo: FakeCaseRepository,
) -> None:
    old = make_case(repo, age_days=36)
    fresh = make_case(repo, age_days=30, household=H2)
    demo = make_case(repo, age_days=36, household=H4, scale=1440)  # 36 case days in 36 minutes
    decided = make_case(repo, age_days=40, household=uuid4(), state=CaseState.decided)

    page = await service.list_cases(
        principal(Role.caseworker), repo=repo, clock=FixedClock(FIXED_NOW), state=CaseState.decided
    )
    open_page = await service.list_cases(
        principal(Role.supervisor), repo=repo, clock=FixedClock(FIXED_NOW)
    )

    rows = {r.case_id: r for r in open_page.items}
    assert (rows[old.id].days_remaining, rows[old.id].at_risk) == (9, True)
    assert (rows[fresh.id].days_remaining, rows[fresh.id].at_risk) == (15, False)
    assert (rows[demo.id].days_remaining, rows[demo.id].at_risk) == (9, True)
    assert decided.id not in rows
    [row] = page.items
    assert (row.case_id, row.days_remaining, row.at_risk) == (decided.id, None, False)


async def test_FR_001_27_list_is_sorted_by_deadline_and_pages_by_cursor(
    repo: FakeCaseRepository,
) -> None:
    cases = [make_case(repo, household=uuid4(), deadline_offset=n % 3) for n in range(7)]
    expected = [c.id for c in sorted(cases, key=lambda c: (c.decision_deadline, c.id))]

    seen: list[UUID] = []
    cursor: str | None = None
    while True:
        page = await service.list_cases(
            principal(Role.caseworker),
            repo=repo,
            clock=FixedClock(FIXED_NOW),
            cursor=cursor,
            limit=3,
        )
        seen += [r.case_id for r in page.items]
        cursor = page.next_cursor
        if cursor is None:
            break

    assert seen == expected


@pytest.mark.parametrize("cursor", ["!!", "bm9wZQ", "x" * 10])
async def test_FR_001_27_invalid_cursor_is_refused(repo: FakeCaseRepository, cursor: str) -> None:
    with pytest.raises(InvalidInput) as refused:
        await service.list_cases(
            principal(Role.caseworker), repo=repo, clock=FixedClock(FIXED_NOW), cursor=cursor
        )

    assert refused.value.fields[0].field == "query.cursor"


async def test_FR_001_26_applicant_list_holds_only_own_household_open_cases(
    repo: FakeCaseRepository,
) -> None:
    own_open = make_case(repo, household=H1)
    make_case(repo, household=H1, state=CaseState.decided)
    make_case(repo, household=H2)

    page = await service.list_cases(
        principal(Role.applicant, H1),
        repo=repo,
        clock=FixedClock(FIXED_NOW),
        state=CaseState.decided,  # ignored for an applicant
    )

    assert [r.case_id for r in page.items] == [own_open.id]


def _decided_override_case(repo: FakeCaseRepository, scale: int = 1440) -> Case:
    case = make_case(repo, household=H4, state=CaseState.decided, scale=scale, age_days=3)
    case.outcome = CaseOutcome.approved
    case.decided_on = case.received_on + timedelta(days=2)
    case.override = CaseOverride(reviewer_id=CW1, reason="SYNTH-REASON-123", determination_id=None)
    repo.coverage.append(
        CoveragePeriod(
            id=uuid4(),
            household_id=H4,
            case_id=case.id,
            start_date=date(2026, 3, 1),
            end_date=date(2027, 2, 28),
            status=CoverageStatus.active,
        )
    )
    return case


async def detail(
    repo: FakeCaseRepository,
    case_id: UUID,
    who: object,
    *,
    names: FakeUserNames | None = None,
    audit: InMemoryAuditWriter | None = None,
) -> object:
    return await service.get_case_detail(
        case_id,
        who,  # type: ignore[arg-type]
        repo=repo,
        users=names or FakeUserNames(),
        audit=audit or InMemoryAuditWriter(),
        clock=FixedClock(FIXED_NOW),
        reference_id="ref-1",
    )


async def test_FR_001_27_case_detail_has_coverage_override_and_demo_clock(
    repo: FakeCaseRepository,
) -> None:
    names = FakeUserNames()
    names.names[CW1] = "Casey Synth"
    decided = _decided_override_case(repo, scale=1440)
    scale_one = _decided_override_case(repo, scale=1)
    scale_one.household_id = H4
    undecided = make_case(repo, household=H4, state=CaseState.determining)
    for case in (decided, scale_one):
        case.open = False

    shown = await detail(repo, decided.id, principal(Role.caseworker), names=names)
    plain = await detail(repo, scale_one.id, principal(Role.caseworker), names=names)
    early = await detail(repo, undecided.id, principal(Role.caseworker), names=names)

    assert shown.demo_clock is True  # type: ignore[attr-defined]
    assert shown.coverage_period.start_date == date(2026, 3, 1)  # type: ignore[attr-defined]
    assert shown.coverage_period.end_date == date(2027, 2, 28)  # type: ignore[attr-defined]
    assert shown.override.reviewer_name == "Casey Synth"  # type: ignore[attr-defined]
    assert plain.demo_clock is False  # type: ignore[attr-defined]
    assert early.coverage_period is None and early.override is None  # type: ignore[attr-defined]


async def test_FR_001_20_applicant_case_detail_omits_override_reason(
    repo: FakeCaseRepository,
) -> None:
    names = FakeUserNames()
    names.names[CW1] = "Casey Synth"
    case = _decided_override_case(repo)

    own = await detail(repo, case.id, principal(Role.applicant, H4), names=names)
    staff = await detail(repo, case.id, principal(Role.caseworker), names=names)

    assert own.override.reason is None  # type: ignore[attr-defined]
    assert own.override.reviewer_name == "Casey Synth"  # type: ignore[attr-defined]
    assert staff.override.reason == "SYNTH-REASON-123"  # type: ignore[attr-defined]


def _event(case: Case, seq: int, actor: UUID | None) -> CaseEvent:
    when = FIXED_NOW + timedelta(minutes=seq)
    return CaseEvent(
        id=uuid4(),
        case_id=case.id,
        household_id=case.household_id,
        seq=seq,
        kind=CaseEventKind.state_changed,
        from_state=CaseState.submitted,
        to_state=CaseState.verifying,
        case_time=when,
        real_time=when,
        actor_type=EventActorType.system if actor is None else EventActorType.user,
        actor_id=actor,
        reason_code="verification_started",
        created_at=when,
    )


async def test_FR_001_10_history_entries_show_system_or_display_name_in_seq_order(
    repo: FakeCaseRepository,
) -> None:
    case = make_case(repo)
    actors = [None, CW1, None, CW2, uuid4()]
    repo.events = [_event(case, n + 1, a) for n, a in reversed(list(enumerate(actors)))]
    names = FakeUserNames()
    names.names |= {CW1: "Casey Synth", CW2: "Riley Synth"}

    page = await service.get_case_history(
        case.id, principal(Role.caseworker), repo=repo, users=names
    )

    assert [e.seq for e in page.items] == [1, 2, 3, 4, 5]
    assert [e.actor for e in page.items] == [
        "System",
        "Casey Synth",
        "System",
        "Riley Synth",
        "Unknown user",
    ]
    assert page.items[1].reason_code == "verification_started"
    assert page.items[1].case_time == page.items[1].real_time == FIXED_NOW + timedelta(minutes=2)
    assert len(names.queries) == 1  # one read for every actor on the page
    assert page.next_cursor is None


async def test_FR_001_27_service_history_pages_by_seq_cursor(repo: FakeCaseRepository) -> None:
    case = make_case(repo)
    repo.events = [_event(case, n, None) for n in range(1, 121)]

    cursors: list[int | None] = []
    cursor = 0
    sizes: list[int] = []
    while True:
        page = await service.get_case_history(
            case.id,
            principal(Role.caseworker),
            repo=repo,
            users=FakeUserNames(),
            cursor=cursor,
            limit=50,
        )
        cursors.append(page.next_cursor)
        sizes.append(len(page.items))
        if page.next_cursor is None:
            break
        cursor = page.next_cursor

    assert cursors == [50, 100, None]
    assert sizes == [50, 50, 20]


async def test_FR_001_27_history_of_missing_case_raises_not_found(
    repo: FakeCaseRepository,
) -> None:
    with pytest.raises(NotFound):
        await service.get_case_history(
            uuid4(), principal(Role.caseworker), repo=repo, users=FakeUserNames()
        )


async def test_FR_001_26_other_household_case_returns_same_404_and_audits_wrong_case(
    repo: FakeCaseRepository, settings: object
) -> None:
    stream = io.StringIO()
    configure_logging(settings, "api", stream=stream)  # type: ignore[arg-type]
    c1 = make_case(repo, household=H1)
    audit = InMemoryAuditWriter()
    applicant = principal(Role.applicant, H2)

    try:
        with pytest.raises(NotFound) as foreign:
            await detail(repo, c1.id, applicant, audit=audit)
        with pytest.raises(NotFound) as missing:
            await detail(repo, uuid4(), applicant, audit=audit)
    finally:
        clear_context()

    assert (foreign.value.message, foreign.value.fields) == (
        missing.value.message,
        missing.value.fields,
    )
    [entry] = [e for e in audit.entries if e.target_id == str(c1.id)]
    assert entry.action is AuditAction.access_refused
    assert entry.outcome is AuditOutcome.refused
    assert entry.reason_code is ReasonCode.wrong_case
    assert entry.target_type == "case"
    assert entry.actor_id == str(applicant.user_id)
    logged = [json.loads(line) for line in stream.getvalue().splitlines()]
    refused = [r for r in logged if r["event"] == "case.access_refused"]
    assert len(refused) == 2
    assert str(H1) not in stream.getvalue() and str(H2) not in stream.getvalue()


async def test_FR_001_26_staff_miss_is_not_audited_as_wrong_case(
    repo: FakeCaseRepository,
) -> None:
    audit = InMemoryAuditWriter()

    with pytest.raises(NotFound):
        await detail(repo, uuid4(), principal(Role.caseworker), audit=audit)

    assert audit.entries == []


async def test_FR_001_26_refused_lookup_never_runs_unscoped(repo: FakeCaseRepository) -> None:
    c1 = make_case(repo, household=H1)
    applicant = principal(Role.applicant, H2)
    scope = Scope.for_principal(applicant)

    with pytest.raises(NotFound):
        await detail(repo, c1.id, applicant)
    await service.list_cases(applicant, repo=repo, clock=FixedClock(FIXED_NOW))

    assert repo.calls  # reads happened
    assert all(call_scope == scope for _, call_scope in repo.calls)
    assert all(call_scope.household_id == H2 for _, call_scope in repo.calls)


async def test_FR_001_25_open_case_closed_before_lookup_gives_null_related_case(
    repo: FakeCaseRepository, workflows: FakeCaseWorkflows
) -> None:
    await submit(repo, workflows, SUB_A)

    async def closed_meanwhile(scope: Scope, household_id: UUID) -> None:
        return None

    repo.find_open_case_id = closed_meanwhile  # type: ignore[method-assign]
    with pytest.raises(Conflict) as refused:
        await submit(repo, workflows, SUB_B)

    assert refusal_of(refused.value) == "household_has_open_case"
    assert related_of(refused.value) is None
