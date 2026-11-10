"""Pick the embedder and reranker from settings. Models load once per process (KD-7)."""

from __future__ import annotations

from functools import cache

from packages.platform.settings import Embedder as EmbedderChoice
from packages.platform.settings import Reranker as RerankerChoice
from packages.platform.settings import Settings
from packages.rag.embeddings import Embedder, FakeEmbedder, QwenEmbedder
from packages.rag.rerank import BgeReranker, FakeReranker, Reranker


@cache
def _embedder(choice: EmbedderChoice) -> Embedder:
    return FakeEmbedder() if choice is EmbedderChoice.fake else QwenEmbedder()


@cache
def _reranker(choice: RerankerChoice) -> Reranker:
    return FakeReranker() if choice is RerankerChoice.fake else BgeReranker()


def get_embedder(settings: Settings) -> Embedder:
    return _embedder(settings.EMBEDDER or EmbedderChoice.qwen)


def get_reranker(settings: Settings) -> Reranker:
    return _reranker(settings.RERANKER or RerankerChoice.bge)
