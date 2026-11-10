from __future__ import annotations

from apps.worker.health import WorkerState, worker_checks
from packages.domain.health import CheckStatus, ReadinessStatus
from packages.platform.health import build_report
from packages.platform.settings import Settings


class FakeCheck:
    def __init__(self, name: str, status: CheckStatus, *, required: bool = True) -> None:
        self.name, self.required, self._status = name, required, status

    async def probe(self) -> CheckStatus:
        return self._status


def checks_for(settings: Settings, state: WorkerState, temporal: CheckStatus) -> list:  # type: ignore[type-arg]
    return worker_checks(
        settings,
        state,
        temporal=FakeCheck("temporal", temporal),
        mongo=FakeCheck("mongo", CheckStatus.ok),
    )


async def test_FR_000_09_worker_ready_true_when_polling(settings: Settings) -> None:
    state = WorkerState(polling=True)

    report = await build_report("worker", checks_for(settings, state, CheckStatus.ok))

    assert report.status is ReadinessStatus.ready


async def test_FR_000_09_worker_not_ready_until_polling(settings: Settings) -> None:
    state = WorkerState(polling=False)

    report = await build_report("worker", checks_for(settings, state, CheckStatus.ok))

    assert report.status is ReadinessStatus.not_ready
    assert {c.name: c.status for c in report.checks}["polling"] is CheckStatus.down


async def test_FR_000_09_worker_ready_false_health_true_when_temporal_down(
    settings: Settings,
) -> None:
    from fastapi.testclient import TestClient

    from packages.platform.health import build_health_app

    app = build_health_app(
        "worker", checks_for(settings, WorkerState(polling=True), CheckStatus.down)
    )
    client = TestClient(app)

    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 503
