from __future__ import annotations

from decimal import Decimal

import pytest

from packages.agents.llm.pricing import cost_usd, to_usage
from packages.platform.settings import Settings


def _priced(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "LLM_PRICE_INPUT_PER_MTOK": Decimal("5"),
            "LLM_PRICE_OUTPUT_PER_MTOK": Decimal("25"),
        }
    )


def test_cost_usd_uses_per_million_token_prices(settings: Settings) -> None:
    usage = {"input_tokens": 2_000, "output_tokens": 400, "total_tokens": 2_400}

    assert cost_usd(usage, _priced(settings)) == pytest.approx(0.02)


def test_cost_usd_is_zero_when_prices_are_not_configured(settings: Settings) -> None:
    assert cost_usd({"input_tokens": 10, "output_tokens": 10}, settings) == 0.0


def test_cost_usd_is_zero_without_usage(settings: Settings) -> None:
    assert cost_usd(None, _priced(settings)) == 0.0


def test_to_usage_builds_agent_run_usage(settings: Settings) -> None:
    usage = to_usage(
        {"input_tokens": 1_000_000, "output_tokens": 0},
        _priced(settings),
        model="some-model",
        provider="anthropic",
        duration_ms=1234,
        recorded=True,
    )

    assert usage.model_dump() == {
        "model": "some-model",
        "provider": "anthropic",
        "input_tokens": 1_000_000,
        "output_tokens": 0,
        "cost_usd": 5.0,
        "duration_ms": 1234,
        "recorded": True,
    }
