"""The case workflow's review interface matches contracts/workflow-review.md (FR-010-01, TC-46).

The contract lives in the metarepo (`specs/010-review-queue/contracts/workflow-review.md`); its
names and payload fields are copied here, so a change on either side fails this test.
"""

from __future__ import annotations

import inspect
from typing import Any, get_type_hints

import pytest
from temporalio import activity, workflow

from apps.worker.activities.review_tasks import ReviewTaskActivities
from apps.worker.review_payloads import ReviewFlagUpdate, ReviewTaskClose
from apps.worker.workflows.review import ReviewHandlers, ReviewLoop
from apps.worker.workflows.review_harness import ReviewHarnessWorkflow
from packages.domain.review import (
    CaseworkerAction,
    CaseworkerActionSignal,
    ReviewTask,
    ReviewTaskState,
)

# contracts/workflow-review.md "Signal" and "Queries".
SIGNAL = "caseworker_action"
QUERY = "open_review_tasks"

# contracts/workflow-review.md "Activities": name -> input type.
ACTIVITIES: dict[str, type[Any]] = {
    "upsert_review_task": ReviewTask,
    "set_review_flag": ReviewFlagUpdate,
    "close_review_task": ReviewTaskClose,
    "record_caseworker_action": CaseworkerAction,
}

# data-model.md: the payload fields every consumer (006, 007, 008, 011) relies on.
SIGNAL_FIELDS = {
    "action_id",
    "task_id",
    "action",
    "reviewer_id",
    "reviewer_role",
    "reason",
    "corrected_value",
    "finding_id",
    "revision_id",
    "submitted_at",
}
TASK_STATE_FIELDS = {
    "task_id",
    "household_id",
    "case_id",
    "workflow_id",
    "reason",
    "cause",
    "evidence_refs",
    "blocking",
    "status",
    "opened_at_case",
    "opened_at_real",
    "reminder_at",
    "escalated_at",
    "flags",
    "decision_deadline",
    "coverage_end",
    "closed_by_action_id",
}


def handler_types(fn: Any) -> dict[str, Any]:
    return get_type_hints(fn, globalns=vars(inspect.getmodule(fn)))


@pytest.mark.parametrize("workflow_class", [ReviewHarnessWorkflow])
def test_FR_010_01_signal_query_and_payloads_match_workflow_contract(
    workflow_class: type[ReviewHandlers],
) -> None:
    definition = workflow._Definition.must_from_class(workflow_class)
    signal_hints = handler_types(ReviewHandlers.caseworker_action)
    query_hints = handler_types(ReviewHandlers.open_review_tasks)
    activity_inputs = {
        activity._Definition.must_from_callable(fn).name: next(
            hint for name, hint in handler_types(fn).items() if name != "return"
        )
        for fn in (
            ReviewTaskActivities.upsert_review_task,
            ReviewTaskActivities.set_review_flag,
            ReviewTaskActivities.close_review_task,
            ReviewTaskActivities.record_caseworker_action,
        )
    }
    open_review = inspect.signature(ReviewLoop.open_review).parameters

    # Signal and query names and payloads.
    assert SIGNAL in definition.signals
    assert QUERY in definition.queries
    assert signal_hints["signal"] is CaseworkerActionSignal
    assert query_hints["return"] == list[ReviewTaskState]
    # Payload models carry exactly the agreed fields and refuse anything else.
    assert set(CaseworkerActionSignal.model_fields) == SIGNAL_FIELDS
    assert set(ReviewTaskState.model_fields) == TASK_STATE_FIELDS
    assert set(ReviewTask.model_fields) == TASK_STATE_FIELDS | {"created_at", "updated_at"}
    assert set(CaseworkerAction.model_fields) == SIGNAL_FIELDS | {
        "household_id",
        "case_id",
        "outcome",
        "rejection_code",
        "applied_at_case",
        "recorded_at",
    }
    for model in (CaseworkerActionSignal, ReviewTaskState, ReviewTask, CaseworkerAction):
        assert model.model_config.get("extra") == "forbid", model.__name__
    # Activities: names and inputs.
    assert activity_inputs == ACTIVITIES
    # Internal entry point: open_review(reason, cause, evidence_refs, *, coverage_end=None).
    assert list(open_review)[1:4] == ["reason", "cause", "evidence_refs"]
    assert open_review["coverage_end"].kind is inspect.Parameter.KEYWORD_ONLY
    assert open_review["coverage_end"].default is None
