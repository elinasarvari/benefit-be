from __future__ import annotations

import json
from collections.abc import Iterator
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import tool
from openinference.instrumentation import TraceConfig
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from packages.agents import tracing
from packages.agents.llm import factory
from packages.agents.llm.fake import prompt_config
from packages.agents.tracing import (
    ALLOWED_ATTRIBUTES,
    MaskingSpanExporter,
    agent_run_context,
    setup_tracing,
)
from packages.platform.settings import LlmMode, LlmProvider, Settings
from tests.packages.agents.conftest import RecordResponse

CASE_ID = UUID("00000000-0000-4000-8000-0000000000c1")
WORKFLOW_ID = "case-000-wf-1"
PROMPT_ID = "verify_docs.extract"
PROMPT_VERSION = "v3"
PLANTED_NAME = "Zelda Testperson"
PLANTED_DOB = "1980-02-29"
PLANTED_SSN = "123-45-6789"
PLANTED_DOCUMENT = "SYNTHETIC pay stub: Acme Fakery paid 4821 dollars gross"
PLANTED = (PLANTED_NAME, PLANTED_DOB, PLANTED_SSN, PLANTED_DOCUMENT)


@pytest.fixture
def traced_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "LLM_PROVIDER": LlmProvider.anthropic,
            "LLM_MODE": LlmMode.fake,
            "TRACING_ENABLED": True,
            "LLM_PRICE_INPUT_PER_MTOK": Decimal("5"),
            "LLM_PRICE_OUTPUT_PER_MTOK": Decimal("25"),
        }
    )


def _prompt_messages() -> list[HumanMessage]:
    return [
        HumanMessage(
            content=(
                f"<data>applicant {PLANTED_NAME}, born {PLANTED_DOB}, id {PLANTED_SSN}. "
                f"{PLANTED_DOCUMENT}</data> Extract the income."
            )
        )
    ]


@pytest.fixture
def run_agent(traced_settings: Settings, record_response: RecordResponse) -> Iterator[Any]:
    """Return a function that runs a fake model and a fake tool the way an agent graph would."""
    messages = _prompt_messages()
    directory = record_response(
        PROMPT_ID,
        PROMPT_VERSION,
        messages,
        {"content": "income 4821", "usage": {"input_tokens": 12, "output_tokens": 5}},
    )
    settings = traced_settings.model_copy(update={"LLM_RECORDINGS_DIR": str(directory)})
    model = factory.build_chat_model(settings)

    @tool
    def read_document(name: str, date_of_birth: str) -> str:
        """Read a synthetic document for an applicant."""
        return PLANTED_DOCUMENT

    @tool
    def broken_lookup(identity_number: str) -> str:
        """Fail with an error message that carries personal data."""
        raise ValueError(f"no match for {identity_number} / {PLANTED_NAME}")

    def run(_: None) -> str:
        read_document.invoke({"name": PLANTED_NAME, "date_of_birth": PLANTED_DOB})
        try:
            broken_lookup.invoke({"identity_number": PLANTED_SSN})
        except ValueError:
            pass
        reply = model.invoke(messages, config=prompt_config(PROMPT_ID, PROMPT_VERSION))
        return str(reply.content)

    def execute() -> str:
        with agent_run_context(CASE_ID, WORKFLOW_ID, "verify_docs", PROMPT_VERSION):
            return str(RunnableLambda(run, name="verify_docs_graph").invoke(None))

    yield execute


@pytest.fixture
def exported(
    traced_settings: Settings, request: pytest.FixtureRequest
) -> Iterator[InMemorySpanExporter]:
    hide = getattr(request, "param", True)
    memory = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(MaskingSpanExporter(memory, traced_settings)))
    config = tracing.trace_config() if hide else TraceConfig()
    tracing.instrument_langchain(provider, config)
    yield memory
    tracing.uninstrument_langchain()


def _llm_span(spans: tuple[ReadableSpan, ...]) -> ReadableSpan:
    return next(s for s in spans if (s.attributes or {}).get("openinference.span.kind") == "LLM")


def test_FR_000_28_spans_tagged_with_case_and_workflow_id(
    exported: InMemorySpanExporter, run_agent: Any
) -> None:
    run_agent()

    spans = exported.get_finished_spans()
    assert len(spans) >= 3
    for span in spans:
        attributes = span.attributes or {}
        assert attributes["session.id"] == str(CASE_ID)
        assert attributes["workflow_id"] == WORKFLOW_ID
        assert attributes["agent"] == "verify_docs"
        assert attributes["prompt.version"] == PROMPT_VERSION
    assert _llm_span(spans).attributes["prompt.id"] == PROMPT_ID  # type: ignore[index]


def test_FR_000_28_span_records_model_provider_tokens_and_cost(
    exported: InMemorySpanExporter, run_agent: Any, traced_settings: Settings
) -> None:
    run_agent()

    attributes = _llm_span(exported.get_finished_spans()).attributes or {}
    assert attributes["llm.provider"] == "anthropic"
    assert attributes["llm.model_name"] == traced_settings.ANTHROPIC_MODEL_ID
    assert attributes["llm.token_count.prompt"] == 12
    assert attributes["llm.token_count.completion"] == 5
    assert attributes["llm.token_count.total"] == 17
    assert attributes["llm.cost.total_usd"] == pytest.approx(0.000185)


@pytest.mark.parametrize(
    "exported", [True, False], ids=["hide_flags_on", "hide_flags_off"], indirect=True
)
def test_FR_000_28_exported_spans_contain_no_personal_data(
    exported: InMemorySpanExporter, run_agent: Any
) -> None:
    run_agent()

    spans = exported.get_finished_spans()
    assert spans
    everything = json.dumps(
        [
            {
                "name": span.name,
                "attributes": dict(span.attributes or {}),
                "events": [(e.name, dict(e.attributes or {})) for e in span.events],
                "status": span.status.description,
                "resource": dict(span.resource.attributes),
            }
            for span in spans
        ],
        default=str,
    )
    for planted in PLANTED:
        assert planted not in everything
    for span in spans:
        assert set(span.attributes or {}) <= ALLOWED_ATTRIBUTES
        assert span.events == ()


class _DownExporter(SpanExporter):
    def __init__(self) -> None:
        self.attempts = 0

    def export(self, spans: Any) -> SpanExportResult:
        self.attempts += 1
        raise ConnectionError("phoenix is down")

    def shutdown(self) -> None:
        return None


def test_FR_000_28_exporter_down_agent_run_succeeds(
    traced_settings: Settings, run_agent: Any
) -> None:
    down = _DownExporter()
    provider = setup_tracing(traced_settings, exporter=down)
    assert provider is not None

    try:
        result = run_agent()
        provider.force_flush(2000)
    finally:
        tracing.shutdown_tracing()

    assert result == "income 4821"
    assert down.attempts >= 1


def test_FR_000_28_tracing_disabled_installs_no_exporter(
    traced_settings: Settings, run_agent: Any
) -> None:
    disabled = traced_settings.model_copy(update={"TRACING_ENABLED": False})

    provider = setup_tracing(disabled)

    assert provider is None
    assert not tracing.is_instrumented()
    assert run_agent() == "income 4821"


def test_exporter_drops_everything_outside_the_allowlist(traced_settings: Settings) -> None:
    from opentelemetry.sdk.trace import Event
    from opentelemetry.trace import Status, StatusCode

    memory = InMemorySpanExporter()
    exporter = MaskingSpanExporter(memory, traced_settings)
    span = ReadableSpan(
        name="step",
        attributes={
            "input.value": PLANTED_NAME,
            "output.value": PLANTED_DOCUMENT,
            "llm.input_messages.0.message.content": PLANTED_SSN,
            "metadata": json.dumps({"workflow_id": "wf", "note": PLANTED_DOB}),
            "tool.name": "read_document",
            "citation.count": 2,
        },
        events=[Event("exception", {"exception.message": PLANTED_SSN})],
        status=Status(StatusCode.ERROR, f"failed for {PLANTED_NAME}"),
    )

    exporter.export([span])

    (kept,) = memory.get_finished_spans()
    assert dict(kept.attributes or {}) == {
        "workflow_id": "wf",
        "tool.name": "read_document",
        "citation.count": 2,
    }
    assert kept.events == ()
    assert kept.status.status_code is StatusCode.ERROR
    assert kept.status.description is None
