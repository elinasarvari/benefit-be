"""Replay model for tests, recorded demos and offline runs (FR-000-20).

Fixtures are JSON files at ``<LLM_RECORDINGS_DIR>/<prompt_id>/<prompt_version>/<sha256>.json``.
The prompt id and version come from the run metadata (see ``prompt_config``); the hash is taken
over the input messages, so changing a prompt or its input needs a new recording.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.messages.ai import UsageMetadata
from langchain_core.output_parsers import JsonOutputParser, PydanticOutputParser
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel

PROMPT_ID_KEY = "prompt_id"
PROMPT_VERSION_KEY = "prompt_version"
UNKNOWN_PROMPT = "unknown"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class RecordedResponseMissing(Exception):
    """No fixture exists for the prompt id, prompt version and input of this call."""


def fingerprint(messages: Sequence[BaseMessage]) -> str:
    """sha256 of the role and content of every input message, in order."""
    canonical = json.dumps(
        [[message.type, message.content] for message in messages],
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def prompt_config(prompt_id: str, prompt_version: str) -> RunnableConfig:
    """Run config that names the prompt, for replay lookup and for the trace attributes."""
    return {"metadata": {PROMPT_ID_KEY: prompt_id, PROMPT_VERSION_KEY: prompt_version}}


def _safe(name: str, label: str) -> str:
    if not _SAFE_NAME.fullmatch(name):
        raise ValueError(f"{label}: {name!r} is not a plain name (letters, digits, '.', '_', '-')")
    return name


class ReplayChatModel(BaseChatModel):
    recordings_dir: str
    llm_provider: str = "replay"
    llm_model: str = "replay"

    @property
    def _llm_type(self) -> str:
        return "replay"

    def _get_ls_params(self, stop: list[str] | None = None, **kwargs: Any) -> Any:
        params = super()._get_ls_params(stop=stop, **kwargs)
        params["ls_provider"] = self.llm_provider
        params["ls_model_name"] = self.llm_model
        return params

    def _recording_path(self, messages: Sequence[BaseMessage], metadata: dict[str, Any]) -> Path:
        prompt_id = _safe(str(metadata.get(PROMPT_ID_KEY, UNKNOWN_PROMPT)), PROMPT_ID_KEY)
        version = _safe(str(metadata.get(PROMPT_VERSION_KEY, UNKNOWN_PROMPT)), PROMPT_VERSION_KEY)
        return Path(self.recordings_dir) / prompt_id / version / f"{fingerprint(messages)}.json"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        metadata = dict(run_manager.metadata) if run_manager else {}
        path = self._recording_path(messages, metadata)
        if not path.is_file():
            raise RecordedResponseMissing(
                f"No recorded response for prompt_id={path.parent.parent.name!r}, "
                f"prompt_version={path.parent.name!r}, input_sha256={path.stem}. "
                f"Expected file {path} in directory {self.recordings_dir!r}. "
                "Record it or fix the prompt metadata (prompt_config)."
            )
        response = json.loads(path.read_text(encoding="utf-8"))["response"]
        usage = response.get("usage")
        message = AIMessage(
            content=response.get("content", ""),
            tool_calls=[
                {"name": call["name"], "args": call["args"], "id": call["id"], "type": "tool_call"}
                for call in response.get("tool_calls", [])
            ],
            usage_metadata=(
                UsageMetadata(
                    input_tokens=usage["input_tokens"],
                    output_tokens=usage["output_tokens"],
                    total_tokens=usage["input_tokens"] + usage["output_tokens"],
                )
                if usage
                else None
            ),
        )
        return ChatResult(generations=[ChatGeneration(message=message)])

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | BaseTool | Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        if tool_choice not in (None, "auto"):
            raise ValueError(
                f"tool_choice={tool_choice!r}: Claude rejects forced tool choice (AG-6)"
            )
        return self.bind(tools=[convert_to_openai_tool(tool) for tool in tools], **kwargs)

    def with_structured_output(
        self,
        schema: dict[str, Any] | type,
        *,
        include_raw: bool = False,
        method: Literal["json_schema"] = "json_schema",
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, Any]:
        if include_raw:
            raise ValueError("include_raw is not supported by ReplayChatModel")
        if isinstance(schema, type) and issubclass(schema, BaseModel):
            return self | PydanticOutputParser(pydantic_object=schema)
        return self | JsonOutputParser()
