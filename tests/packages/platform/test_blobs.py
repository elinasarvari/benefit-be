"""TC-46: the BlobStore interface and its two implementations (ADR-002-03, KD-14)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from packages.platform.blobs import BlobStore, InvalidBlobKey, LocalBlobStore, MemoryBlobStore


def _key() -> str:
    return f"{uuid.uuid4()}/{uuid.uuid4()}"


@pytest.fixture(params=["local", "memory"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> BlobStore:
    return LocalBlobStore(tmp_path) if request.param == "local" else MemoryBlobStore()


def test_FR_002_17_blobstore_put_get_idempotent_and_traversal_refused(
    store: BlobStore, tmp_path: Path
) -> None:
    key = _key()
    assert store.get(key) is None
    store.put(key, b"%PDF-synthetic")
    assert store.get(key) == b"%PDF-synthetic"

    store.put(key, b"%PDF-synthetic")  # the same bytes again
    assert store.get(key) == b"%PDF-synthetic"

    for bad in ("../escape", f"../{_key()}", f"{uuid.uuid4()}/../../x", "a/b", "", "/etc/passwd"):
        with pytest.raises(InvalidBlobKey):
            store.put(bad, b"x")
        with pytest.raises(InvalidBlobKey):
            store.get(bad)


def test_FR_002_17_local_put_same_bytes_leaves_one_file(tmp_path: Path) -> None:
    store = LocalBlobStore(tmp_path)
    key = _key()
    store.put(key, b"abc")
    store.put(key, b"abc")
    files = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert len(files) == 1
