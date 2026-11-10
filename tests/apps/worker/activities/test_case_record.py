from __future__ import annotations

import io
import logging
import uuid

import pytest
from temporalio.testing import ActivityEnvironment

from apps.worker.activities.case_record import CaseRecordActivities
from packages.domain.case import CaseChange
from packages.platform.logging import configure_logging
from packages.platform.scope import Scope, ScopeKind
from packages.platform.settings import Settings
from tests.apps.worker.case_factories import SYNTH_REASON, approved, new_ids


class FakeRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[CaseChange, Scope]] = []

    async def apply(self, change: CaseChange, scope: Scope) -> bool:
        self.calls.append((change, scope))
        return len(self.calls) == 1


async def test_FR_001_20_record_case_change_never_logs_override_reason(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    stream = io.StringIO()
    configure_logging(settings, "worker", stream=stream)
    case_id, household_id = new_ids()
    change = approved(case_id, household_id, 4, override_reason=SYNTH_REASON, task_id=uuid.uuid4())
    repository = FakeRepository()
    activities = CaseRecordActivities(repository)

    with caplog.at_level(logging.DEBUG):
        await ActivityEnvironment().run(activities.record_case_change, change)
        await ActivityEnvironment().run(activities.record_case_change, change)  # retry

    logged = stream.getvalue() + caplog.text
    assert "case_change.recorded" in logged
    assert SYNTH_REASON not in logged
    assert str(case_id) in logged
    [(_, scope), _] = repository.calls
    assert scope.kind is ScopeKind.worker
    assert (scope.case_id, scope.household_id) == (case_id, household_id)
