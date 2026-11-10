from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from temporalio.client import Client

from packages.domain.health import CheckStatus
from packages.platform.health import PROBE_TIMEOUT_S, Check, HttpCheck, MongoCheck
from packages.platform.settings import Settings


@dataclass
class WorkerState:
    polling: bool = False


class PollingCheck:
    name = "polling"
    required = True

    def __init__(self, state: WorkerState) -> None:
        self._state = state

    async def probe(self) -> CheckStatus:
        return CheckStatus.ok if self._state.polling else CheckStatus.down


class TemporalCheck:
    name = "temporal"
    required = True

    def __init__(self, address: str, namespace: str) -> None:
        self._address, self._namespace = address, namespace

    async def probe(self) -> CheckStatus:
        client = await Client.connect(self._address, namespace=self._namespace)
        healthy = await client.service_client.check_health(
            timeout=timedelta(seconds=PROBE_TIMEOUT_S)
        )
        return CheckStatus.ok if healthy else CheckStatus.down


def worker_checks(
    settings: Settings,
    state: WorkerState,
    *,
    temporal: Check | None = None,
    mongo: Check | None = None,
) -> list[Check]:
    checks: list[Check] = [
        temporal or TemporalCheck(settings.TEMPORAL_ADDRESS, settings.TEMPORAL_NAMESPACE),
        mongo or MongoCheck(settings.MONGO_URL_APP.get_secret_value(), settings.MONGO_DB),
        PollingCheck(state),
    ]
    if settings.TRACING_ENABLED:
        endpoint = settings.TRACING_OTLP_ENDPOINT
        checks.append(HttpCheck("tracing", endpoint.split("/v1/")[0] + "/healthz", required=False))
    return checks
