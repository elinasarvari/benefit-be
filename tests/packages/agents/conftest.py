from __future__ import annotations

import json
import os
import socket
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any, NoReturn

import pytest
from langchain_core.messages import BaseMessage

from packages.agents.llm.fake import fingerprint

RecordResponse = Callable[[str, str, Sequence[BaseMessage], dict[str, Any]], Path]


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Keep live_llm tests out of every run except `make smoke-ai` (which sets LLM_MODE=live).

    The folder rule in tests/conftest.py also marks them `unit`, so `-m unit` would select them.
    """
    if os.environ.get("LLM_MODE") == "live":
        return
    live = [item for item in items if item.get_closest_marker("live_llm")]
    if live:
        config.hook.pytest_deselected(items=live)
        items[:] = [item for item in items if item not in live]


@pytest.fixture
def record_response(tmp_path: Path) -> RecordResponse:
    """Write a replay fixture for one input into a per-test recordings directory."""

    def record(
        prompt_id: str, version: str, messages: Sequence[BaseMessage], response: dict[str, Any]
    ) -> Path:
        digest = fingerprint(messages)
        path = tmp_path / prompt_id / version / f"{digest}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "prompt_id": prompt_id,
                    "prompt_version": version,
                    "input_sha256": digest,
                    "response": response,
                }
            ),
            encoding="utf-8",
        )
        return tmp_path

    return record


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Any attempt to open a network connection fails the test (FR-000-20)."""

    def refuse(*_: Any, **__: Any) -> NoReturn:
        raise AssertionError("network access attempted in a test that must stay offline")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    yield
