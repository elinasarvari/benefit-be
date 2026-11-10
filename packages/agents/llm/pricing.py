from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from packages.domain.agent_run import AgentRunUsage
from packages.platform.settings import Settings

_PER_MILLION = Decimal(1_000_000)


def cost_from_tokens(input_tokens: int, output_tokens: int, settings: Settings) -> float:
    """Cost in USD from the configured prices. Zero when a price is not configured."""
    input_price = settings.LLM_PRICE_INPUT_PER_MTOK or Decimal(0)
    output_price = settings.LLM_PRICE_OUTPUT_PER_MTOK or Decimal(0)
    total = (input_price * input_tokens + output_price * output_tokens) / _PER_MILLION
    return float(total)


def cost_usd(usage_metadata: Mapping[str, Any] | None, settings: Settings) -> float:
    if not usage_metadata:
        return 0.0
    return cost_from_tokens(
        usage_metadata.get("input_tokens", 0), usage_metadata.get("output_tokens", 0), settings
    )


def to_usage(
    usage_metadata: Mapping[str, Any] | None,
    settings: Settings,
    *,
    model: str,
    provider: str,
    duration_ms: int,
    recorded: bool,
) -> AgentRunUsage:
    usage = usage_metadata or {}
    return AgentRunUsage(
        model=model,
        provider=provider,
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        cost_usd=cost_usd(usage, settings),
        duration_ms=duration_ms,
        recorded=recorded,
    )
