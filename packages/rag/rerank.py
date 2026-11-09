from __future__ import annotations

from typing import Any, Protocol

from packages.rag.embeddings import tokens


class Reranker(Protocol):
    def rerank(self, query: str, passages: list[str]) -> list[float]: ...


class FakeReranker:
    """Scores a passage by how many of the query's words it contains."""

    def rerank(self, query: str, passages: list[str]) -> list[float]:
        wanted = set(tokens(query))
        return [len(wanted & set(tokens(passage))) / (len(wanted) or 1) for passage in passages]


class BgeReranker:
    def __init__(self) -> None:
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:
            raise RuntimeError("install the ml group first: uv sync --group ml") from exc
        self._model: Any = CrossEncoder("BAAI/bge-reranker-v2-m3", device="mps")

    def rerank(self, query: str, passages: list[str]) -> list[float]:
        return [float(s) for s in self._model.predict([(query, p) for p in passages])]
