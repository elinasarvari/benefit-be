from __future__ import annotations

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

CITED_FACT_PROMPT_ID = "smoke.cited_fact"
PROMPT_VERSION = "v1"


class CitedFact(BaseModel):
    """A short answer that carries the reference it was taken from (AG-11)."""

    answer: str = Field(description="The answer in one sentence")
    citation: str = Field(description="The source reference, for example a policy chunk ID")


def cited_fact_messages() -> list[BaseMessage]:
    return [
        SystemMessage(content="Answer only from the data block. Cite the chunk ID you used."),
        HumanMessage(
            content=(
                "<data>chunk POLICY-001-03: The synthetic benefit review window is 30 days."
                "</data>\nHow long is the review window?"
            )
        ),
    ]
