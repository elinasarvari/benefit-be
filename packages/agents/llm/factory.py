"""The one place a chat model is built, and the only module that imports a provider SDK (AG-5).

Model IDs, timeouts and retries come from settings. ``temperature`` and ``top_p`` are never set
(AG-7), and tool choice is never forced (AG-6).
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from botocore.config import Config  # type: ignore[import-untyped]
from langchain_anthropic import ChatAnthropic
from langchain_aws import ChatBedrockConverse
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.runnables import Runnable

from packages.agents.llm.fake import ReplayChatModel
from packages.agents.tracing import setup_tracing
from packages.platform.settings import LlmMode, LlmProvider, Settings, load_settings


class LiveModelRefused(RuntimeError):
    """A live model call was requested where only recorded or fake models are allowed."""


def _model_id(settings: Settings) -> str:
    if settings.LLM_PROVIDER is LlmProvider.anthropic:
        return settings.ANTHROPIC_MODEL_ID
    return settings.BEDROCK_MODEL_ID


def build_chat_model(settings: Settings) -> BaseChatModel:
    if settings.LLM_MODE is not LlmMode.live:
        return ReplayChatModel(
            recordings_dir=settings.LLM_RECORDINGS_DIR,
            llm_provider=settings.LLM_PROVIDER.value,
            llm_model=_model_id(settings),
        )
    if os.environ.get("CI", "").lower() == "true":
        raise LiveModelRefused("LLM_MODE=live is refused when CI=true; use recorded or fake")
    if settings.LLM_PROVIDER is LlmProvider.anthropic:
        return ChatAnthropic(
            model=settings.ANTHROPIC_MODEL_ID,
            api_key=settings.ANTHROPIC_API_KEY,
            timeout=settings.LLM_TIMEOUT_SECONDS,
            max_retries=settings.LLM_MAX_RETRIES,
            max_tokens=settings.LLM_MAX_TOKENS,
        )
    if os.environ.get("AWS_PROFILE") == "":
        # `.env` lines like `AWS_PROFILE=` reach the process as an empty value, and boto3
        # treats that as a profile named "" instead of "not set".
        del os.environ["AWS_PROFILE"]
    timeout = settings.LLM_TIMEOUT_SECONDS
    return ChatBedrockConverse(
        model_id=settings.BEDROCK_MODEL_ID,
        region_name=settings.AWS_REGION,
        credentials_profile_name=settings.AWS_PROFILE or None,
        max_tokens=settings.LLM_MAX_TOKENS,
        config=Config(
            connect_timeout=timeout,
            read_timeout=timeout,
            retries={"max_attempts": settings.LLM_MAX_RETRIES, "mode": "standard"},
        ),
    )


@lru_cache(maxsize=1)
def get_chat_model() -> BaseChatModel:
    """The process-wide model client (AG-9). Starts tracing the first time it is called."""
    settings = load_settings()
    setup_tracing(settings)
    return build_chat_model(settings)


def structured(
    schema: type | dict[str, Any], *, model: BaseChatModel | None = None
) -> Runnable[LanguageModelInput, Any]:
    """Native JSON-schema structured output; tool choice is not forced (AG-6)."""
    return (model or get_chat_model()).with_structured_output(schema, method="json_schema")
