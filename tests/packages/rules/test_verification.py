"""The pay stub check (002; FR-002-03, -08, -14, -15, -22, -23, -24; ADR-002-02, ADR-002-05).

Synthetic names and employers only (PR-3).
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from packages.domain.case import PayFrequency
from packages.domain.verification import (
    Dismissal,
    FindingCause,
    IdentityMatch,
    MissingDocumentKind,
    PayStubDetails,
    ReferenceAnswer,
    VerificationResult,
)
from packages.rules.verification import (
    CheckInput,
    DeclaredJob,
    DeclaredMember,
    check_pay_stubs,
    stub_assignments,
)

NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://benefit-case-platform.example/verification")
CASE = uuid.uuid5(NAMESPACE, "case")
HOUSEHOLD = uuid.uuid5(NAMESPACE, "household")
AS_OF = date(2026, 6, 1)
TEN = Decimal(10)


def uid(name: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, name)


def job(declared: str = "3000.00", employer: str = "Acme Works", name: str = "job") -> DeclaredJob:
    return DeclaredJob(uid(name), employer, Decimal(declared))


def member(
    name: str = "Ana Rivera",
    jobs: list[DeclaredJob] | None = None,
    *,
    zero_income: bool = False,
    key: str = "ana",
) -> DeclaredMember:
    return DeclaredMember(
        uid(f"member-{key}"), name, zero_income, [job()] if jobs is None else jobs
    )


def stub(
    gross: str = "3000.00",
    *,
    frequency: PayFrequency = PayFrequency.monthly,
    pay_date: date = AS_OF - timedelta(days=10),
    employee: str = "Ana Rivera",
    employer: str = "Acme Works",
    key: str | None = None,
) -> PayStubDetails:
    return PayStubDetails(
        document_id=uid(f"stub-{key or f'{gross}-{pay_date}-{frequency}-{employee}-{employer}'}"),
        case_id=CASE,
        household_id=HOUSEHOLD,
        employee_name=employee,
        employer=employer,
        frequency=frequency,
        gross_pay=Decimal(gross),
        pay_date=pay_date,
    )


def matches(*members: DeclaredMember) -> dict[uuid.UUID, ReferenceAnswer]:
    return {
        m.member_id: ReferenceAnswer(member_id=m.member_id, identity=IdentityMatch.match)
        for m in members
    }


def run(
    members: list[DeclaredMember],
    stubs: list[PayStubDetails],
    *,
    threshold: str = "10",
    references: dict[uuid.UUID, ReferenceAnswer] | None = None,
    dismissed: dict[uuid.UUID, Dismissal] | None = None,
    as_of: date = AS_OF,
) -> VerificationResult:
    return check_pay_stubs(
        CheckInput(
            case_id=CASE,
            as_of_date=as_of,
            members=members,
            stubs=stubs,
            references=matches(*members) if references is None else references,
            dismissed=dismissed or {},
        ),
        threshold_percent=Decimal(threshold),
    )


def causes(result: VerificationResult) -> list[FindingCause]:
    return [f.cause for f in result.findings]


# ---- FR-002-03 --------------------------------------------------------------------------


def test_FR_002_03_mismatch_above_threshold_records_finding_per_job() -> None:
    ana = member(jobs=[job("3000.00"), job("1000.00", "Birch Mills", "job2")])
    stubs = [stub("3400.00"), stub("1000.00", employer="Birch Mills")]

    result = run([ana], stubs)

    [finding] = result.findings
    assert finding.cause is FindingCause.income_mismatch
    assert finding.member_id == ana.member_id
    assert finding.job_id == uid("job")
    assert finding.document_id == stubs[0].document_id
    assert finding.declared_monthly == Decimal("3000.00")
    assert finding.documented_monthly == Decimal("3400.00")
    assert result.needs_review is True
    assert result.complete is True


def test_FR_002_03_difference_at_or_below_ten_percent_no_finding() -> None:
    for gross in ("3300.00", "3100.00", "2700.00"):
        result = run([member()], [stub(gross)])

        assert result.findings == [], gross
        assert result.needs_review is False


def test_FR_002_03_weekly_and_biweekly_converted_before_compare() -> None:
    weekly = run([member()], [stub("900.00", frequency=PayFrequency.weekly)])
    biweekly = run([member()], [stub("1800.00", frequency=PayFrequency.biweekly)])

    [w] = weekly.findings
    [b] = biweekly.findings
    assert w.documented_monthly == Decimal("3900.00")
    assert w.conversion == "weekly 900.00 x 52 / 12 = 3900.00"
    assert b.documented_monthly == Decimal("3900.00")
    assert b.conversion == "biweekly 1800.00 x 26 / 12 = 3900.00"
    # 692.31 x 52 / 12 = 3000.01: the converted figure matches, the raw one would not
    assert run([member()], [stub("692.31", frequency=PayFrequency.weekly)]).findings == []


def test_FR_002_03_threshold_is_configurable() -> None:
    stubs = [stub("3210.00")]  # 7% above declared

    assert run([member()], stubs, threshold="10").findings == []
    [finding] = run([member()], stubs, threshold="5").findings
    assert finding.cause is FindingCause.income_mismatch


def test_FR_002_03_conflicting_stubs_same_pay_period_finding() -> None:
    first = stub("3000.00", key="a")
    second = stub("3600.00", key="b")

    result = run([member()], [first, second])

    [finding] = result.findings
    assert finding.cause is FindingCause.conflicting_stubs
    assert finding.job_id == uid("job")
    assert finding.document_id in {first.document_id, second.document_id}
    assert result.needs_review is True


def test_FR_002_03_latest_stub_sets_monthly_income() -> None:
    older = stub("5000.00", pay_date=AS_OF - timedelta(days=30))
    newer = stub("3000.00", pay_date=AS_OF - timedelta(days=5))

    assert run([member()], [older, newer]).findings == []
    [finding] = run(
        [member()],
        [
            stub("3000.00", pay_date=AS_OF - timedelta(days=30)),
            stub("5000.00", pay_date=AS_OF - timedelta(days=5)),
        ],
    ).findings
    assert finding.documented_monthly == Decimal("5000.00")


def test_FR_002_03_unmatched_employer_leaves_job_missing() -> None:
    result = run([member()], [stub("9000.00", employer="Other Co")])

    assert result.findings == []
    [item] = result.missing_items
    assert item.kind is MissingDocumentKind.no_stub
    assert item.job_id == uid("job")
    assert result.complete is False
    # whitespace and case do not matter
    assert run([member()], [stub("3000.00", employer="  ACME   works ")]).missing_items == []


def test_FR_002_03_two_jobs_with_one_employer_match_by_closest_declared_income() -> None:
    ana = member(jobs=[job("3000.00", name="job-a"), job("1000.00", name="job-b")])
    stubs = [stub("1000.00", key="b", pay_date=AS_OF - timedelta(days=3)), stub("3000.00", key="a")]

    forward = run([ana], stubs)
    backward = run([ana], list(reversed(stubs)))

    assert forward.findings == [] and forward.missing_items == []
    assert forward == backward


# ---- FR-002-08, FR-002-14 ----------------------------------------------------------------


def test_FR_002_08_result_has_no_eligibility_field() -> None:
    result = run([member()], [stub("5000.00")])

    dumped = set(result.model_dump())
    assert dumped == set(VerificationResult.model_fields)
    assert not {n for n in dumped if "elig" in n or "decision" in n or "outcome" in n}
    assert not any("elig" in c.value for c in FindingCause)


def test_FR_002_14_unknown_employee_name_different_person() -> None:
    stranger = stub("9000.00", employee="Zed Nobody")

    result = run([member()], [stranger])

    different = [f for f in result.findings if f.cause is FindingCause.different_person]
    [finding] = different
    assert finding.document_id == stranger.document_id
    assert finding.member_id is None and finding.job_id is None
    # not used as income: no mismatch finding, the job has no stub
    assert FindingCause.income_mismatch not in causes(result)
    assert [m.kind for m in result.missing_items] == [MissingDocumentKind.no_stub]


def test_FR_002_14_two_different_person_stubs_get_different_finding_ids() -> None:
    result = run(
        [member()], [stub(employee="Zed Nobody", key="x"), stub(employee="Yan Other", key="y")]
    )

    ids = {f.finding_id for f in result.findings if f.cause is FindingCause.different_person}
    assert len(ids) == 2


def test_FR_002_14_name_match_is_case_folded() -> None:
    result = run([member("Ana Rivera")], [stub("3000.00", employee="ANA  rivera")])

    assert result.findings == []
    assert result.missing_items == []


# ---- FR-002-15 ---------------------------------------------------------------------------


def test_FR_002_15_job_without_stub_is_missing() -> None:
    ana = member()

    result = run([ana], [])

    [item] = result.missing_items
    assert (item.member_id, item.job_id, item.kind) == (
        ana.member_id,
        uid("job"),
        MissingDocumentKind.no_stub,
    )
    assert FindingCause.income_mismatch not in causes(result)
    assert result.complete is False


def test_FR_002_15_stub_older_than_45_days_is_missing() -> None:
    too_old = run([member()], [stub(pay_date=AS_OF - timedelta(days=46))])
    fine = run([member()], [stub(pay_date=AS_OF - timedelta(days=45))])

    assert [m.kind for m in too_old.missing_items] == [MissingDocumentKind.stub_too_old]
    assert too_old.complete is False
    assert fine.missing_items == []
    assert fine.complete is True


def test_FR_002_15_zero_income_needs_no_document() -> None:
    result = run([member(jobs=[], zero_income=True)], [])

    assert result.missing_items == []
    assert result.complete is True
    assert result.findings == []


# ---- FR-002-22, FR-002-24 ----------------------------------------------------------------


def test_FR_002_22_identity_mismatch_when_name_dob_differ() -> None:
    ana = member()
    refs = {
        ana.member_id: ReferenceAnswer(member_id=ana.member_id, identity=IdentityMatch.no_match)
    }

    result = run([ana], [stub()], references=refs)

    [finding] = result.findings
    assert finding.cause is FindingCause.identity_mismatch
    assert finding.identity is IdentityMatch.no_match
    assert finding.member_id == ana.member_id
    assert result.identity_verified is False
    assert result.needs_review is True


def test_FR_002_24_no_record_gives_identity_mismatch_no_record() -> None:
    ana = member()
    explicit = {
        ana.member_id: ReferenceAnswer(member_id=ana.member_id, identity=IdentityMatch.no_record)
    }

    for refs in (explicit, {}):
        result = run([ana], [stub()], references=refs)

        [finding] = result.findings
        assert finding.cause is FindingCause.identity_mismatch
        assert finding.identity is IdentityMatch.no_record
        assert result.identity_verified is False


def test_FR_002_24_dismissed_identity_mismatch_counts_as_verified() -> None:
    ana = member()
    first = run([ana], [stub()], references={})
    [finding] = first.findings
    dismissal = Dismissal(
        reviewer_id=uid("reviewer"),
        reason="checked by phone",
        dismissed_at=datetime(2026, 6, 1, tzinfo=UTC),
    )

    result = run([ana], [stub()], references={}, dismissed={finding.finding_id: dismissal})

    [again] = result.findings
    assert again.finding_id == finding.finding_id
    assert again.dismissed == dismissal
    assert result.identity_verified is True
    assert result.needs_review is False


def test_FR_002_23_finding_ids_are_stable_across_runs_and_input_order() -> None:
    members = [member(), member("Bo Chen", [job("1000.00", "Birch Mills", "job-b")], key="bo")]
    stubs = [
        stub("3600.00"),
        stub("1500.00", employee="Bo Chen", employer="Birch Mills"),
        stub(employee="Zed Nobody", key="z"),
    ]

    first = run(members, stubs)
    second = run(list(reversed(members)), list(reversed(stubs)))

    assert first == second
    assert len({f.finding_id for f in first.findings}) == len(first.findings) == 3


# ---- SC-002-07 ---------------------------------------------------------------------------

_names = st.sampled_from(["Ana Rivera", "Bo Chen", "Cy Dunn"])
_employers = st.sampled_from(["Acme Works", "Birch Mills", "Cedar Foods"])


@st.composite
def _inputs(
    draw: st.DrawFn,
) -> tuple[list[DeclaredMember], list[PayStubDetails], dict[uuid.UUID, ReferenceAnswer]]:
    members = []
    for index, name in enumerate(["Ana Rivera", "Bo Chen", "Cy Dunn"][: draw(st.integers(1, 3))]):
        employers = draw(st.lists(_employers, unique=True, max_size=2))
        jobs = [
            DeclaredJob(
                uid(f"job-{index}-{n}"),
                employer,
                Decimal(draw(st.integers(0, 600000))) / 100,
            )
            for n, employer in enumerate(employers)
        ]
        members.append(DeclaredMember(uid(f"m-{index}"), name, not jobs, jobs))
    stubs = [
        PayStubDetails(
            document_id=uid(f"s-{n}"),
            case_id=CASE,
            household_id=HOUSEHOLD,
            employee_name=draw(_names),
            employer=draw(_employers),
            frequency=draw(st.sampled_from(list(PayFrequency))),
            gross_pay=Decimal(draw(st.integers(1, 600000))) / 100,
            pay_date=AS_OF - timedelta(days=draw(st.integers(0, 90))),
        )
        for n in range(draw(st.integers(0, 6)))
    ]
    references = {
        m.member_id: ReferenceAnswer(
            member_id=m.member_id, identity=draw(st.sampled_from(list(IdentityMatch)))
        )
        for m in members
        if draw(st.booleans())
    }
    return members, stubs, references


@settings(max_examples=100, deadline=None)
@given(_inputs(), st.randoms(use_true_random=False))
def test_SC_002_07_same_inputs_same_result(
    data: tuple[list[DeclaredMember], list[PayStubDetails], dict[uuid.UUID, ReferenceAnswer]],
    rnd: random.Random,
) -> None:
    members, stubs, references = data
    first = run(members, stubs, references=references)
    shuffled_stubs, shuffled_members = list(stubs), list(members)
    rnd.shuffle(shuffled_stubs)
    rnd.shuffle(shuffled_members)

    assert run(members, stubs, references=references) == first
    assert run(shuffled_members, shuffled_stubs, references=references) == first
    assert first.complete == (not first.missing_items)
    assert first.needs_review == any(f.dismissed is None for f in first.findings)


# ---- ADR-002-04: the check and the determination's facts share one assignment ---------------


def test_FR_002_23_assignment_collapses_whitespace_and_gives_each_stub_to_one_job() -> None:
    ana = member(jobs=[job("3000.00", name="job-a"), job("1000.00", name="job-b")])
    spaced = stub("3000.00", employee="ANA  rivera", key="a")  # the check matches it (TC-11)
    other = stub("1000.00", key="b")

    assignments = stub_assignments([ana], [spaced, other])

    by_job = {a.job_id: a.document_id for a in assignments}
    assert by_job == {uid("job-a"): spaced.document_id, uid("job-b"): other.document_id}
    assert len(assignments) == 2  # a stub counts for one job only (income not doubled)
    assert assignments == stub_assignments([ana], [other, spaced])  # order never matters
