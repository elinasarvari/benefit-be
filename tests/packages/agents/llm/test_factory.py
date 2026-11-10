from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest
from langchain_core.language_models import BaseChatModel
from pydantic import SecretStr

from packages.agents.llm import factory
from packages.platform.settings import LlmMode, LlmProvider, Settings

REPO_ROOT = Path(__file__).resolve().parents[4]
PROVIDER_SDKS = ("anthropic", "langchain_anthropic", "langchain_aws", "boto3")


@pytest.fixture(autouse=True)
def _not_in_ci(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("CI", raising=False)
    factory.get_chat_model.cache_clear()
    yield
    factory.get_chat_model.cache_clear()


@pytest.fixture
def anthropic_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "LLM_MODE": LlmMode.live,
            "LLM_PROVIDER": LlmProvider.anthropic,
            "ANTHROPIC_API_KEY": SecretStr("test-key-not-real"),
            "ANTHROPIC_MODEL_ID": "anthropic-model-from-settings",
            "LLM_TIMEOUT_SECONDS": 42.0,
            "LLM_MAX_RETRIES": 1,
            "LLM_MAX_TOKENS": 777,
        }
    )


@pytest.fixture
def bedrock_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "LLM_MODE": LlmMode.live,
            "LLM_PROVIDER": LlmProvider.bedrock,
            "AWS_REGION": "us-east-1",
            "BEDROCK_MODEL_ID": "bedrock-model-from-settings",
            "LLM_TIMEOUT_SECONDS": 42.0,
            "LLM_MAX_RETRIES": 1,
            "LLM_MAX_TOKENS": 777,
        }
    )


def test_FR_000_12_anthropic_provider_builds_chat_anthropic(anthropic_settings: Settings) -> None:
    model = factory.build_chat_model(anthropic_settings)

    assert type(model).__name__ == "ChatAnthropic"


def test_FR_000_12_bedrock_provider_builds_chat_bedrock_converse(
    bedrock_settings: Settings,
) -> None:
    model = factory.build_chat_model(bedrock_settings)

    assert type(model).__name__ == "ChatBedrockConverse"


def test_FR_000_12_bedrock_ignores_empty_aws_profile_variable(
    monkeypatch: pytest.MonkeyPatch, bedrock_settings: Settings
) -> None:
    monkeypatch.setenv("AWS_PROFILE", "")

    model = factory.build_chat_model(bedrock_settings)

    assert type(model).__name__ == "ChatBedrockConverse"


def test_FR_000_12_model_id_read_from_selected_provider_setting(
    anthropic_settings: Settings, bedrock_settings: Settings
) -> None:
    anthropic = factory.build_chat_model(anthropic_settings)
    bedrock = factory.build_chat_model(bedrock_settings)

    assert anthropic.model == "anthropic-model-from-settings"  # type: ignore[attr-defined]
    assert bedrock.model_id == "bedrock-model-from-settings"  # type: ignore[attr-defined]


def test_FR_000_12_provider_sdk_imported_only_in_factory() -> None:
    allowed = REPO_ROOT / "packages" / "agents" / "llm" / "factory.py"
    offenders: list[str] = []
    for folder in ("packages", "apps", "scripts"):
        for path in (REPO_ROOT / folder).rglob("*.py"):
            if path == allowed or ".venv" in path.parts:
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                    if isinstance(node, ast.ImportFrom)
                    else []
                )
                if any(name.split(".")[0] in PROVIDER_SDKS for name in names):
                    offenders.append(str(path.relative_to(REPO_ROOT)))

    assert offenders == []


def test_FR_000_13_anthropic_client_has_timeout_and_bounded_retries(
    anthropic_settings: Settings,
) -> None:
    model = factory.build_chat_model(anthropic_settings)

    assert model.default_request_timeout == 42.0  # type: ignore[attr-defined]
    assert model.max_retries == 1  # type: ignore[attr-defined]
    assert model.max_tokens == 777  # type: ignore[attr-defined]


def test_FR_000_13_bedrock_client_has_timeout_and_bounded_retries(
    bedrock_settings: Settings,
) -> None:
    model = factory.build_chat_model(bedrock_settings)

    config = model.client.meta.config  # type: ignore[attr-defined]
    assert config.connect_timeout == 42.0
    assert config.read_timeout == 42.0
    # botocore reports max_attempts (retries) as total attempts: one try plus LLM_MAX_RETRIES.
    assert config.retries == {"mode": "standard", "total_max_attempts": 2}
    assert model.max_tokens == 777  # type: ignore[attr-defined]


def test_chat_model_created_once_per_process(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    built: list[Settings] = []
    monkeypatch.setattr(factory, "load_settings", lambda: settings)
    monkeypatch.setattr(factory, "setup_tracing", lambda _settings: None)
    real_build = factory.build_chat_model

    def counting_build(chosen: Settings) -> BaseChatModel:
        built.append(chosen)
        return real_build(chosen)

    monkeypatch.setattr(factory, "build_chat_model", counting_build)

    first = factory.get_chat_model()
    second = factory.get_chat_model()

    assert first is second
    assert len(built) == 1


@pytest.mark.parametrize("provider", ["anthropic", "bedrock"])
def test_chat_model_sets_no_temperature_or_top_p(
    provider: str, anthropic_settings: Settings, bedrock_settings: Settings
) -> None:
    chosen = anthropic_settings if provider == "anthropic" else bedrock_settings

    model = factory.build_chat_model(chosen)

    assert getattr(model, "temperature", None) is None
    assert getattr(model, "top_p", None) is None
    params = model._get_ls_params()
    assert "ls_temperature" not in params or params["ls_temperature"] is None


def test_FR_000_20_live_model_refused_in_ci(
    monkeypatch: pytest.MonkeyPatch, anthropic_settings: Settings
) -> None:
    monkeypatch.setenv("CI", "true")

    with pytest.raises(factory.LiveModelRefused, match="CI"):
        factory.build_chat_model(anthropic_settings)
