"""Live smoke test (SC-000-04). Calls the configured provider; run only by `make smoke-ai`.

Excluded from every other run (addopts `-m 'not live_llm'`, plus a collection guard in the
folder's conftest.py for runs that pass their own `-m`), and refused when CI=true.
"""

from __future__ import annotations

import base64
import io
import os
import time
from collections.abc import Iterator
from decimal import Decimal

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from packages.agents.llm import factory
from packages.agents.llm.pricing import to_usage
from packages.platform.settings import LlmMode, Settings
from tests.packages.agents.llm.smoke_support import CitedFact, cited_fact_messages

pytestmark = pytest.mark.live_llm

# The autouse test environment forces APP_ENV=test and LLM_MODE=fake; keep what `make smoke-ai` set.
REQUESTED_APP_ENV = os.environ.get("APP_ENV")
REQUESTED_LLM_MODE = os.environ.get("LLM_MODE")
UNUSED_SECRET = "x" * 40


class PdfField(BaseModel):
    value: str = Field(description="The value of the requested field exactly as printed")
    citation: str = Field(description="The label printed next to the value")


@pytest.fixture(autouse=True)
def _live_run_only(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    if os.environ.get("CI", "").lower() == "true":
        pytest.fail("live_llm tests must never run in CI (CI=true)", pytrace=False)
    for key, original in (("APP_ENV", REQUESTED_APP_ENV), ("LLM_MODE", REQUESTED_LLM_MODE)):
        if original is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, original)
    yield


@pytest.fixture
def live_settings() -> Settings:
    settings = Settings(
        MONGO_URL_APP="mongodb://unused.invalid/",
        MONGO_URL_OWNER="mongodb://unused.invalid/",
        SESSION_SECRET=UNUSED_SECRET,
        SYSTEM_CREDENTIAL_SECRET=UNUSED_SECRET[:-1] + "y",
        TRACING_ENABLED=False,
    )
    assert settings.LLM_MODE is LlmMode.live
    return settings


@pytest.fixture
def live_model(live_settings: Settings) -> BaseChatModel:
    return factory.build_chat_model(live_settings)


def _stamped_pdf() -> bytes:
    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=letter)
    page.setFont("Helvetica-Bold", 20)
    page.drawString(72, 720, "SYNTHETIC DOCUMENT - NOT REAL")
    page.setFont("Helvetica", 14)
    page.drawString(72, 680, "Monthly gross income: 4821.00 USD")
    page.save()
    return buffer.getvalue()


def test_live_plain_call_returns(live_model: BaseChatModel) -> None:
    reply = live_model.invoke([HumanMessage(content="Reply with the single word pong.")])

    assert "pong" in str(reply.text).lower()


def test_live_cited_structured_output_parses(live_model: BaseChatModel) -> None:
    result = factory.structured(CitedFact, model=live_model).invoke(cited_fact_messages())

    assert isinstance(result, CitedFact)
    assert "POLICY-001-03" in result.citation


def test_live_field_extracted_from_stamped_pdf(live_model: BaseChatModel) -> None:
    document = {
        "type": "file",
        "mime_type": "application/pdf",
        "base64": base64.b64encode(_stamped_pdf()).decode("ascii"),
    }
    message = HumanMessage(
        content=[
            {"type": "text", "text": "Extract the monthly gross income from this document."},
            document,
        ]
    )

    result = factory.structured(PdfField, model=live_model).invoke([message])

    assert isinstance(result, PdfField)
    assert "4821" in result.value


def test_live_tool_binds_with_auto_tool_choice(live_model: BaseChatModel) -> None:
    @tool
    def lookup_limit(topic: str) -> str:
        """Look up a synthetic limit by topic."""
        return f"limit for {topic}"

    bound = live_model.bind_tools([lookup_limit], tool_choice="auto")

    reply = bound.invoke(
        [HumanMessage(content="Use the lookup_limit tool to find the household size limit.")]
    )

    assert isinstance(reply, AIMessage)
    assert reply.tool_calls[0]["name"] == "lookup_limit"


def test_live_usage_has_tokens_and_cost(live_model: BaseChatModel, live_settings: Settings) -> None:
    priced = live_settings.model_copy(
        update={
            "LLM_PRICE_INPUT_PER_MTOK": live_settings.LLM_PRICE_INPUT_PER_MTOK or Decimal("5"),
            "LLM_PRICE_OUTPUT_PER_MTOK": live_settings.LLM_PRICE_OUTPUT_PER_MTOK or Decimal("25"),
        }
    )
    started = time.perf_counter()

    reply = live_model.invoke([HumanMessage(content="Reply with the single word pong.")])

    assert isinstance(reply, AIMessage)
    usage = to_usage(
        reply.usage_metadata,
        priced,
        model=str(getattr(live_model, "model", None) or getattr(live_model, "model_id", "")),
        provider=priced.LLM_PROVIDER.value,
        duration_ms=int((time.perf_counter() - started) * 1000),
        recorded=False,
    )
    assert usage.input_tokens > 0
    assert usage.output_tokens > 0
    assert usage.cost_usd > 0
    assert usage.recorded is False
