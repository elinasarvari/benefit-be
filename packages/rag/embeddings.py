"""Embedding models behind one interface (KD-4, KD-7, FR-000-24).

`FakeEmbedder` is deterministic and has no model files, so CI and tests run without the ML
libraries. `QwenEmbedder` imports them lazily; they are in the optional `ml` dependency group.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Protocol

DIMENSIONS = 1024
QUERY_INSTRUCTION = (
    "Instruct: Given a policy question, retrieve the relevant manual section\nQuery: "
)


class Embedder(Protocol):
    def embed_query(self, text: str) -> list[float]: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


class FakeEmbedder:
    """Hashes each word into one of 1024 buckets, then normalises to unit length."""

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    @staticmethod
    def _embed(text: str) -> list[float]:
        vector = [0.0] * DIMENSIONS
        for word in tokens(text):
            digest = hashlib.sha256(f"benefit-fake-embedder:{word}".encode()).digest()
            vector[int.from_bytes(digest[:4], "big") % DIMENSIONS] += 1.0
        norm = math.sqrt(sum(v * v for v in vector))
        return [v / norm for v in vector] if norm else vector


class QwenEmbedder:
    """Qwen3-Embedding-0.6B, 1024 dimensions. Only queries get the instruction prefix."""

    def __init__(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError("install the ml group first: uv sync --group ml") from exc
        self._model: Any = SentenceTransformer("Qwen/Qwen3-Embedding-0.6B", device="mps")

    def embed_query(self, text: str) -> list[float]:
        return [
            float(v)
            for v in self._model.encode(QUERY_INSTRUCTION + text, normalize_embeddings=True)
        ]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [
            [float(v) for v in row] for row in self._model.encode(texts, normalize_embeddings=True)
        ]
