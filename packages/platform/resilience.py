"""Timeouts and bounded retries with backoff for every outbound call (FR-000-13, security.md)."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable

import httpx

from packages.platform.settings import Settings

BACKOFF_BASE_S = 0.5
BACKOFF_CAP_S = 8.0
RETRYABLE: tuple[type[Exception], ...] = (httpx.TransportError, TimeoutError)


async def call_with_retry[T](
    call: Callable[[], Awaitable[T]],
    *,
    timeout_s: float,
    max_attempts: int,
    retry_on: tuple[type[Exception], ...] = RETRYABLE,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    jitter: Callable[[], float] = lambda: random.uniform(0.5, 1.0),  # noqa: S311 - not security
) -> T:
    """Run `call` with a per-attempt timeout. Retries only `retry_on` errors, at most
    `max_attempts` times in all, with exponential backoff and jitter between attempts."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    for attempt in range(1, max_attempts + 1):
        try:
            return await asyncio.wait_for(call(), timeout=timeout_s)
        except retry_on:
            if attempt == max_attempts:
                raise
            await sleep(min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2 ** (attempt - 1)) * jitter())
    raise AssertionError("unreachable")  # pragma: no cover


def http_client(settings: Settings) -> httpx.AsyncClient:
    """HTTP client with the configured timeout and TLS verification on."""
    return httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT_SECONDS, verify=True)
