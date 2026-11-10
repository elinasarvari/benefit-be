from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from temporalio.activity import _Definition as ActivityDefinition
from temporalio.workflow import _Definition as WorkflowDefinition

from apps.worker.main import WORKFLOWS, build_activities, main
from packages.platform.clock import FixedClock
from packages.platform.mongo import make_client
from packages.platform.settings import Settings


def test_FR_000_04_worker_missing_setting_exits_naming_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)  # so no .env is read
    for name in ("SESSION_SECRET", "SYSTEM_CREDENTIAL_SECRET", "MONGO_URL_APP", "MONGO_URL_OWNER"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(SystemExit) as exit_info:
        main()

    assert exit_info.value.code == 2
    assert "setting SESSION_SECRET" in capsys.readouterr().err


def activity_name(fn: Any) -> str:
    definition = ActivityDefinition.from_callable(fn)
    assert definition is not None
    return str(definition.name)


async def test_FR_001_03_worker_registers_case_workflow_and_activities(settings: Settings) -> None:
    mongo = make_client(settings.MONGO_URL_APP.get_secret_value())  # lazy: connects on first use
    try:
        clock = FixedClock(datetime(2026, 3, 17, tzinfo=UTC))
        activities = build_activities(settings, clock, mongo)
    finally:
        await mongo.close()

    names = {activity_name(fn) for fn in activities}
    workflows = {WorkflowDefinition.must_from_class(cls).name for cls in WORKFLOWS}
    assert {"record_case_change", "verify_documents", "determine_eligibility"} <= names
    assert {"SmokeWorkflow", "CaseWorkflow"} <= workflows
