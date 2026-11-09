"""Delete the Phoenix project so old traces don't point at case IDs the seed creates again.

Best effort: if Phoenix is not running, print a warning and carry on (R13).
"""

from __future__ import annotations

import sys

import httpx

from packages.platform.settings import load_settings


def main() -> int:
    settings = load_settings()
    if not settings.TRACING_ENABLED:
        return 0
    base = settings.TRACING_OTLP_ENDPOINT.split("/v1/")[0]
    try:
        response = httpx.delete(f"{base}/v1/projects/{settings.TRACING_PROJECT}", timeout=5)
    except httpx.HTTPError:
        print("warning: Phoenix is not reachable, traces were not cleared", file=sys.stderr)
        return 0
    if response.status_code in (200, 204, 404):
        print("cleared Phoenix traces")
    else:
        print(
            f"warning: Phoenix returned {response.status_code}, traces were not cleared",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
