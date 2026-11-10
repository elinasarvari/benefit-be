"""Poll every component and print a table (`make health`). Exit 0 only if all are usable."""

from __future__ import annotations

import subprocess  # noqa: S404 - runs the temporal CLI
import sys
from collections.abc import Callable

import httpx

from packages.platform.settings import Settings, load_settings

OK_STATES = {"ready", "degraded", "ok"}


def http_status(url: str, *, json_status: bool) -> str:
    try:
        response = httpx.get(url, timeout=3)
    except httpx.HTTPError:
        return "down"
    if json_status:
        return str(response.json().get("status", "down")) if response.content else "down"
    return "ok" if response.status_code == 200 else "down"


def temporal_status(settings: Settings) -> str:
    try:
        done = subprocess.run(  # noqa: S603 - fixed command
            ["temporal", "operator", "cluster", "health", "--address", settings.TEMPORAL_ADDRESS],  # noqa: S607
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "down"
    return "ready" if done.returncode == 0 else "down"


def collect(settings: Settings) -> dict[str, str]:
    checks: dict[str, Callable[[], str]] = {
        "api": lambda: http_status(
            f"http://localhost:{settings.API_PORT}/api/v1/ready", json_status=True
        ),
        "worker": lambda: http_status(
            f"http://localhost:{settings.WORKER_HEALTH_PORT}/ready", json_status=True
        ),
        "temporal": lambda: temporal_status(settings),
        "web": lambda: http_status(settings.WEB_ORIGIN, json_status=False),
    }
    if settings.TRACING_ENABLED:
        base = settings.TRACING_OTLP_ENDPOINT.split("/v1/")[0]
        checks["phoenix"] = lambda: http_status(f"{base}/healthz", json_status=False)
    return {name: check() for name, check in checks.items()}


def main() -> int:
    results = collect(load_settings())
    width = max(len(name) for name in results)
    for name, status in results.items():
        print(f"{name:<{width}}  {status}")
    return 0 if all(status in OK_STATES for status in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
