"""`RedeterminationsDueWorkflow`: the daily schedule's action (007, FR-007-01, FR-007-07).

One activity opens the due cases; each is started as an abandoned child `CaseWorkflow` with the
case's own workflow ID, so the case outlives this run (TW-3). A case already started is success.
"""

from __future__ import annotations

from temporalio import workflow
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

with workflow.unsafe.imports_passed_through():
    from apps.worker.policies import UNTIL_DONE
    from apps.worker.workflows.calendar import chicago_date
    from packages.domain.case import workflow_id_for
    from packages.domain.renewal import DueRedeterminations, DueRedeterminationsRequest


@workflow.defn(name="RedeterminationsDueWorkflow")
class RedeterminationsDueWorkflow:
    @workflow.run
    async def run(self, request: DueRedeterminationsRequest | None = None) -> int:
        """Returns how many cases it started now (already started ones are not counted)."""
        as_of = request.as_of_date if request is not None else chicago_date(workflow.now())
        due: DueRedeterminations = await workflow.execute_activity(
            "open_due_redeterminations",
            DueRedeterminationsRequest(as_of_date=as_of),
            result_type=DueRedeterminations,
            **UNTIL_DONE,
        )
        started = 0
        for start in due.starts:
            try:
                await workflow.start_child_workflow(
                    "CaseWorkflow",
                    start,
                    id=workflow_id_for(start.case_id),
                    parent_close_policy=workflow.ParentClosePolicy.ABANDON,
                    id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                )
            except WorkflowAlreadyStartedError:
                continue
            started += 1
        return started
