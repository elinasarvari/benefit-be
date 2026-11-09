from __future__ import annotations

import asyncio
import ssl

import httpx
import pytest

from packages.platform.resilience import call_with_retry, http_client
from packages.platform.settings import Settings


class Flaky:
    def __init__(self, failures: int, error: Exception) -> None:
        self.calls, self._failures, self._error = 0, failures, error

    async def __call__(self) -> str:
        self.calls += 1
        if self.calls <= self._failures:
            raise self._error
        return "ok"


class FakeSleep:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


async def test_FR_000_13_call_times_out() -> None:
    async def hangs() -> None:
        await asyncio.sleep(30)

    with pytest.raises(TimeoutError):
        await call_with_retry(hangs, timeout_s=0.05, max_attempts=1)


async def test_FR_000_13_retries_bounded_with_exponential_backoff() -> None:
    flaky, sleep = Flaky(failures=99, error=httpx.ConnectError("down")), FakeSleep()

    with pytest.raises(httpx.ConnectError):
        await call_with_retry(flaky, timeout_s=1, max_attempts=4, sleep=sleep, jitter=lambda: 1.0)

    assert flaky.calls == 4
    assert sleep.delays == [0.5, 1.0, 2.0]


async def test_FR_000_13_recovers_when_a_retry_succeeds() -> None:
    flaky, sleep = Flaky(failures=2, error=httpx.ReadTimeout("slow")), FakeSleep()

    result = await call_with_retry(flaky, timeout_s=1, max_attempts=5, sleep=sleep)

    assert result == "ok"
    assert flaky.calls == 3


async def test_FR_000_13_non_retryable_error_not_retried() -> None:
    flaky, sleep = Flaky(failures=99, error=ValueError("bad input")), FakeSleep()

    with pytest.raises(ValueError, match="bad input"):
        await call_with_retry(flaky, timeout_s=1, max_attempts=5, sleep=sleep)

    assert flaky.calls == 1
    assert sleep.delays == []


async def test_FR_000_13_backoff_is_capped() -> None:
    flaky, sleep = Flaky(failures=99, error=httpx.ConnectError("down")), FakeSleep()

    with pytest.raises(httpx.ConnectError):
        await call_with_retry(flaky, timeout_s=1, max_attempts=9, sleep=sleep, jitter=lambda: 1.0)

    assert max(sleep.delays) == 8.0


async def test_FR_000_13_http_client_has_timeout_and_tls_verify(settings: Settings) -> None:
    async with http_client(settings) as client:
        assert client.timeout == httpx.Timeout(settings.HTTP_TIMEOUT_SECONDS)
        pool = client._transport._pool  # type: ignore[attr-defined]  # noqa: SLF001
        assert pool._ssl_context.verify_mode == ssl.CERT_REQUIRED  # noqa: SLF001
