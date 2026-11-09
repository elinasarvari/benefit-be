from __future__ import annotations

from datetime import timedelta

from temporalio.client import Client

from packages.domain.health import CheckStatus
from packages.platform.health import PROBE_TIMEOUT_S, Check, HttpCheck, MongoCheck
from packages.platform.settings import Settings


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


def build_checks(settings: Settings) -> list[Check]:
    checks: list[Check] = [
        MongoCheck(settings.MONGO_URL_APP.get_secret_value(), settings.MONGO_DB),
        TemporalCheck(settings.TEMPORAL_ADDRESS, settings.TEMPORAL_NAMESPACE),
    ]
    if settings.TRACING_ENABLED:
        endpoint = settings.TRACING_OTLP_ENDPOINT
        checks.append(HttpCheck("tracing", endpoint.split("/v1/")[0] + "/healthz", required=False))
    return checks
