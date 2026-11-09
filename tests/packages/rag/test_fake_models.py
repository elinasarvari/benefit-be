from __future__ import annotations

import math
import subprocess
import sys

from hypothesis import given
from hypothesis import strategies as st

from packages.platform.settings import Embedder, Reranker, Settings
from packages.rag.embeddings import DIMENSIONS, FakeEmbedder
from packages.rag.factory import get_embedder, get_reranker
from packages.rag.rerank import FakeReranker


@given(st.text(max_size=300))
def test_FR_000_24_fake_embedder_vectors_are_unit_length_and_repeatable(text: str) -> None:
    embedder = FakeEmbedder()

    vector = embedder.embed_query(text)

    assert len(vector) == DIMENSIONS == 1024
    norm = math.sqrt(sum(v * v for v in vector))
    assert math.isclose(norm, 1.0, rel_tol=1e-9) or norm == 0.0
    assert embedder.embed_query(text) == vector


def test_FR_000_24_fake_embedder_deterministic_across_processes() -> None:
    script = (
        "import hashlib; from packages.rag.embeddings import FakeEmbedder;"
        "v = FakeEmbedder().embed_query('income limit for a household of three');"
        "print(hashlib.sha256(repr(v).encode()).hexdigest())"
    )
    runs = {
        subprocess.run(  # noqa: S603
            [sys.executable, "-c", script], capture_output=True, text=True, check=True
        ).stdout
        for _ in range(2)
    }

    assert len(runs) == 1


def test_FR_000_24_fake_embedder_puts_texts_that_share_words_closer() -> None:
    embedder = FakeEmbedder()
    query = embedder.embed_query("household income limit")
    near = embedder.embed_documents(["the household income limit applies"])[0]
    far = embedder.embed_documents(["pay stub recency window"])[0]

    def cosine(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    assert cosine(query, near) > cosine(query, far)


def test_FR_000_24_fake_reranker_orders_by_token_overlap() -> None:
    scores = FakeReranker().rerank(
        "income limit", ["unrelated text", "the income limit is 138 percent", "limit only"]
    )

    assert scores[1] > scores[2] > scores[0]


def test_FR_000_24_settings_pick_fake_models_in_test(settings: Settings) -> None:
    assert settings.EMBEDDER is Embedder.fake and settings.RERANKER is Reranker.fake
    assert isinstance(get_embedder(settings), FakeEmbedder)
    assert isinstance(get_reranker(settings), FakeReranker)
