"""Tracing of AI runs to Arize Phoenix over OpenTelemetry (FR-000-28, AG-15, R17).

Nothing leaves the process except the attributes in ``ALLOWED_ATTRIBUTES``: the exporter drops
everything else, so message content, documents and exception messages cannot reach Phoenix even
if an instrumentation starts emitting them.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from openinference.instrumentation import TraceConfig, using_attributes
from openinference.instrumentation.langchain import LangChainInstrumentor
from openinference.semconv.resource import ResourceAttributes
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import Status

from packages.agents.llm.fake import PROMPT_ID_KEY, PROMPT_VERSION_KEY
from packages.agents.llm.pricing import cost_from_tokens
from packages.platform.logging import mask_pii
from packages.platform.settings import Settings

EXPORT_TIMEOUT_MS = 2_000
MAX_QUEUE_SIZE = 256
MAX_BATCH_SIZE = 64
SCHEDULE_DELAY_MS = 1_000

# Copied from data-model.md "Trace span". Keep both in sync.
PASSTHROUGH_ATTRIBUTES = frozenset(
    {
        "session.id",
        "openinference.span.kind",
        "llm.provider",
        "llm.model_name",
        "llm.token_count.prompt",
        "llm.token_count.completion",
        "llm.token_count.total",
        "llm.cost.total_usd",
        "tool.name",
        "validation.outcome",
        "citation.count",
    }
)
# Run metadata keys (a JSON string in the `metadata` attribute) mapped to the exported attribute.
METADATA_ATTRIBUTES = {
    "workflow_id": "workflow_id",
    "agent": "agent",
    PROMPT_ID_KEY: "prompt.id",
    PROMPT_VERSION_KEY: "prompt.version",
}
ALLOWED_ATTRIBUTES = PASSTHROUGH_ATTRIBUTES | frozenset(METADATA_ATTRIBUTES.values())

_PROMPT_TOKENS = "llm.token_count.prompt"
_COMPLETION_TOKENS = "llm.token_count.completion"
_COST = "llm.cost.total_usd"


def _masked(value: Any) -> Any:
    if isinstance(value, str):
        return str(mask_pii(None, "", {"value": value})["value"])
    return value


class MaskingSpanExporter(SpanExporter):
    """Wraps an exporter and passes on only allowlisted attributes, no events, no error text."""

    def __init__(self, wrapped: SpanExporter, settings: Settings) -> None:
        self._wrapped = wrapped
        self._settings = settings

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return self._wrapped.export([self._mask(span) for span in spans])

    def shutdown(self) -> None:
        self._wrapped.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return self._wrapped.force_flush(timeout_millis)

    def _mask(self, span: ReadableSpan) -> ReadableSpan:
        source: Mapping[str, Any] = span.attributes or {}
        kept: dict[str, Any] = {
            key: _masked(source[key]) for key in PASSTHROUGH_ATTRIBUTES if key in source
        }
        kept.update(self._from_metadata(source.get("metadata")))
        if _COST not in kept and (_PROMPT_TOKENS in kept or _COMPLETION_TOKENS in kept):
            kept[_COST] = cost_from_tokens(
                int(kept.get(_PROMPT_TOKENS, 0)),
                int(kept.get(_COMPLETION_TOKENS, 0)),
                self._settings,
            )
        return ReadableSpan(
            name=span.name,
            context=span.context,
            parent=span.parent,
            resource=span.resource,
            attributes=kept,
            kind=span.kind,
            status=Status(span.status.status_code),
            start_time=span.start_time,
            end_time=span.end_time,
            instrumentation_scope=span.instrumentation_scope,
        )

    @staticmethod
    def _from_metadata(raw: Any) -> dict[str, Any]:
        if not isinstance(raw, str):
            return {}
        try:
            metadata = json.loads(raw)
        except ValueError:
            return {}
        if not isinstance(metadata, dict):
            return {}
        return {
            exported: _masked(metadata[key])
            for key, exported in METADATA_ATTRIBUTES.items()
            if isinstance(metadata.get(key), str)
        }


def trace_config() -> TraceConfig:
    """Hide flags that keep content out of the spans before they reach the exporter."""
    return TraceConfig(
        hide_inputs=True,
        hide_outputs=True,
        hide_input_messages=True,
        hide_output_messages=True,
        hide_input_images=True,
        hide_input_text=True,
        hide_output_text=True,
        hide_embedding_vectors=True,
        hide_embeddings_text=True,
        hide_llm_invocation_parameters=True,
        hide_llm_tools=True,
        hide_prompts=True,
        hide_choices=True,
        hide_retrieval_documents=True,
    )


def instrument_langchain(provider: TracerProvider, config: TraceConfig | None = None) -> None:
    LangChainInstrumentor().instrument(
        tracer_provider=provider, config=config or trace_config(), skip_dep_check=True
    )


def uninstrument_langchain() -> None:
    LangChainInstrumentor().uninstrument()


def is_instrumented() -> bool:
    return bool(LangChainInstrumentor().is_instrumented_by_opentelemetry)


_provider: TracerProvider | None = None


def setup_tracing(
    settings: Settings, *, exporter: SpanExporter | None = None
) -> TracerProvider | None:
    """Start exporting traces. Does nothing when ``TRACING_ENABLED`` is false.

    The batch processor has a bounded queue and a short export timeout, so when Phoenix is down
    spans are dropped and the run carries on. Calling it again returns the running provider.
    """
    global _provider
    if not settings.TRACING_ENABLED:
        return None
    if _provider is not None:
        return _provider
    target = exporter or OTLPSpanExporter(
        endpoint=settings.TRACING_OTLP_ENDPOINT, timeout=EXPORT_TIMEOUT_MS // 1000
    )
    provider = TracerProvider(
        resource=Resource.create({ResourceAttributes.PROJECT_NAME: settings.TRACING_PROJECT})
    )
    provider.add_span_processor(
        BatchSpanProcessor(
            MaskingSpanExporter(target, settings),
            max_queue_size=MAX_QUEUE_SIZE,
            max_export_batch_size=MAX_BATCH_SIZE,
            schedule_delay_millis=SCHEDULE_DELAY_MS,
            export_timeout_millis=EXPORT_TIMEOUT_MS,
        )
    )
    instrument_langchain(provider)
    _provider = provider
    return provider


def shutdown_tracing() -> None:
    global _provider
    if _provider is None:
        return
    uninstrument_langchain()
    _provider.shutdown()
    _provider = None


@contextmanager
def agent_run_context(
    case_id: UUID | str,
    workflow_id: str,
    agent: str,
    prompt_version: str,
    *,
    prompt_id: str | None = None,
) -> Iterator[None]:
    """Tag every span of one agent run with its case (as the session) and workflow."""
    metadata = {"workflow_id": workflow_id, "agent": agent, PROMPT_VERSION_KEY: prompt_version}
    if prompt_id is not None:
        metadata[PROMPT_ID_KEY] = prompt_id
    with using_attributes(session_id=str(case_id), metadata=metadata):
        yield
