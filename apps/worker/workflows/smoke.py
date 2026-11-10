from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from apps.worker.policies import DEFAULT
    from apps.worker.workflows.calendar import chicago_date
    from packages.domain.smoke import SmokeInput, SmokeResult


@workflow.defn(name="SmokeWorkflow")
class SmokeWorkflow:
    """Proves the plumbing: Chicago date from workflow time, a durable timer, one activity.

    The real `CaseWorkflow` belongs to feature 001. Workflow IDs follow `case-{case_id}` (TW-3).
    """

    @workflow.run
    async def run(self, data: SmokeInput) -> SmokeResult:
        started_on = chicago_date(workflow.now())
        await workflow.sleep(timedelta(seconds=data.delay_seconds))
        result = await workflow.execute_activity("smoke_step", data, result_type=str, **DEFAULT)
        return SmokeResult(started_on=started_on, activity_result=result)
