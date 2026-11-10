import uuid
from datetime import UTC, date, datetime, timedelta

from temporalio import workflow
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from apps.worker.workflows.calendar import case_now_wf, case_today_wf, chicago_date, days_between
from packages.domain.case import CaseClock


@workflow.defn(name="CaseClockProbe", sandboxed=False)
class CaseClockProbe:
    """Test-only workflow: reads the case clock once at start and once after a one-day timer."""

    @workflow.run
    async def run(self, clock: CaseClock) -> tuple[list[datetime], date]:
        first = [workflow.now(), case_now_wf(clock)]
        await workflow.sleep(timedelta(days=1))
        second = [workflow.now(), case_now_wf(clock)]
        return first + second, case_today_wf(clock)


async def test_FR_001_13_case_now_wf_uses_workflow_now() -> None:
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        real_start = await env.get_current_time()
        clock = CaseClock(
            real_start=real_start,
            case_start=datetime(2026, 3, 17, 17, 0, tzinfo=UTC),
            scale=1440,
        )
        async with Worker(env.client, task_queue="test-case-clock", workflows=[CaseClockProbe]):
            times, today = await env.client.execute_workflow(
                CaseClockProbe.run, clock, id=f"probe-{uuid.uuid4()}", task_queue="test-case-clock"
            )

    wf_now_1, case_1, wf_now_2, case_2 = times
    assert today == chicago_date(case_2)
    for wf_now, case_time in ((wf_now_1, case_1), (wf_now_2, case_2)):
        assert case_time == clock.case_start + (wf_now - real_start) * 1440
    # One skipped real day is 1440 case days in the demo profile.
    assert (case_2 - case_1) == (wf_now_2 - wf_now_1) * 1440
    assert wf_now_2 - wf_now_1 >= timedelta(days=1)


def test_FR_000_27_chicago_date_across_utc_midnight() -> None:
    assert chicago_date(datetime(2026, 7, 1, 5, 30, tzinfo=UTC)) == date(2026, 7, 1)
    assert chicago_date(datetime(2026, 7, 1, 4, 30, tzinfo=UTC)) == date(2026, 6, 30)
    assert chicago_date(datetime(2026, 1, 10, 5, 30, tzinfo=UTC)) == date(2026, 1, 9)


def test_FR_000_27_days_between_counts_calendar_days_in_chicago() -> None:
    start = datetime(2026, 3, 8, 7, 0, tzinfo=UTC)  # 01:00 CST, the day DST starts
    end = datetime(2026, 3, 10, 6, 0, tzinfo=UTC)  # 01:00 CDT

    assert days_between(start, end) == 2
