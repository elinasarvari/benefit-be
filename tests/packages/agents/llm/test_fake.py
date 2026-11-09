from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from packages.agents.llm import factory
from packages.agents.llm.fake import (
    RecordedResponseMissing,
    ReplayChatModel,
    fingerprint,
    prompt_config,
)
from packages.platform.settings import LlmMode, Settings
from tests.packages.agents.llm.smoke_support import (
    CITED_FACT_PROMPT_ID,
    PROMPT_VERSION,
    CitedFact,
    cited_fact_messages,
)

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "llm"


@pytest.fixture
def fake_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"LLM_MODE": LlmMode.fake, "LLM_RECORDINGS_DIR": str(FIXTURES)}
    )


def test_FR_000_20_fake_mode_returns_structured_output_without_network(
    no_network: None, fake_settings: Settings
) -> None:
    model = factory.build_chat_model(fake_settings)
    chain = factory.structured(CitedFact, model=model).with_config(
        prompt_config(CITED_FACT_PROMPT_ID, PROMPT_VERSION)
    )

    result = chain.invoke(cited_fact_messages())

    assert isinstance(result, CitedFact)
    assert result.citation == "POLICY-001-03"


def test_FR_000_20_missing_recorded_response_fails_with_clear_message(
    tmp_path: Path, fake_settings: Settings
) -> None:
    model = ReplayChatModel(recordings_dir=str(tmp_path))
    messages = [HumanMessage(content="no recording exists for this")]

    with pytest.raises(RecordedResponseMissing) as raised:
        model.invoke(messages, config=prompt_config("absent.prompt", "v9"))

    text = str(raised.value)
    assert "absent.prompt" in text
    assert "v9" in text
    assert fingerprint(messages) in text
    assert str(tmp_path) in text


def test_FR_000_20_live_model_refused_in_tests(
    monkeypatch: pytest.MonkeyPatch, fake_settings: Settings
) -> None:
    monkeypatch.setenv("CI", "true")
    live = fake_settings.model_copy(update={"LLM_MODE": LlmMode.live})

    with pytest.raises(factory.LiveModelRefused):
        factory.build_chat_model(live)


def test_replay_returns_recorded_text_and_usage_for_plain_call(
    no_network: None, fake_settings: Settings
) -> None:
    model = factory.build_chat_model(fake_settings)

    reply = model.invoke(
        [HumanMessage(content="Reply with the single word pong.")],
        config=prompt_config("smoke.ping", "v1"),
    )

    assert reply.content == "pong"
    assert reply.usage_metadata is not None
    assert reply.usage_metadata["total_tokens"] == 17


def test_replay_supports_bind_tools_with_auto_tool_choice(
    no_network: None, fake_settings: Settings
) -> None:
    @tool
    def lookup_limit(topic: str) -> str:
        """Look up a synthetic limit."""
        return f"limit for {topic}"

    model = factory.build_chat_model(fake_settings).bind_tools([lookup_limit], tool_choice="auto")

    reply = model.invoke(
        [HumanMessage(content="Look up the household size limit.")],
        config=prompt_config("smoke.tool_call", "v1"),
    )

    assert isinstance(reply, AIMessage)
    assert reply.tool_calls[0]["name"] == "lookup_limit"
    assert reply.tool_calls[0]["args"] == {"topic": "household size"}


@pytest.mark.parametrize("forced", ["any", "lookup_limit"])
def test_replay_refuses_forced_tool_choice(fake_settings: Settings, forced: str) -> None:
    @tool
    def lookup_limit(topic: str) -> str:
        """Look up a synthetic limit."""
        return topic

    model = factory.build_chat_model(fake_settings)

    with pytest.raises(ValueError, match="tool_choice"):
        model.bind_tools([lookup_limit], tool_choice=forced)


def test_replay_rejects_prompt_id_that_escapes_the_recordings_directory(tmp_path: Path) -> None:
    model = ReplayChatModel(recordings_dir=str(tmp_path))

    with pytest.raises(ValueError, match="prompt_id"):
        model.invoke([HumanMessage(content="x")], config=prompt_config("../../etc/passwd", "v1"))


def test_fingerprint_is_stable_and_sensitive_to_content() -> None:
    first = fingerprint([HumanMessage(content="a")])

    assert first == fingerprint([HumanMessage(content="a")])
    assert first != fingerprint([HumanMessage(content="b")])
    assert len(first) == 64


def test_starter_fixtures_are_keyed_by_their_own_contents() -> None:
    for path in FIXTURES.rglob("*.json"):
        recorded = json.loads(path.read_text(encoding="utf-8"))

        assert path.parent.parent.name == recorded["prompt_id"]
        assert path.parent.name == recorded["prompt_version"]
        assert path.stem == recorded["input_sha256"]
