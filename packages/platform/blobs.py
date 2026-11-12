"""File storage behind an interface (KD-14, constitution IX, ADR-002-03).

Keys are `"{household_id}/{document_id}"`. The API writes the bytes through a `BlobStore`; nothing
else opens files under `BLOB_DIR`. Neither implementation logs keys or bytes (PL-11).
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Protocol

_KEY = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class InvalidBlobKey(ValueError):
    """The key is not `"{household_id}/{document_id}"`; nothing was read or written."""


class BlobStore(Protocol):
    def put(self, key: str, data: bytes) -> None:
        """Store `data` under `key`. Putting the same bytes again is a no-op."""
        ...

    def get(self, key: str) -> bytes | None: ...


def check_key(key: str) -> str:
    if not _KEY.fullmatch(key):
        raise InvalidBlobKey("blob key must be '<household uuid>/<document uuid>'")
    return key


class MemoryBlobStore:
    """The fake: same contract, no disk."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    def put(self, key: str, data: bytes) -> None:
        self.blobs[check_key(key)] = data

    def get(self, key: str) -> bytes | None:
        return self.blobs.get(check_key(key))


class LocalBlobStore:
    """Files under one directory. A put writes a temp file and renames it, so a retry is safe."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        path = (self._root / check_key(key)).resolve()
        if self._root not in path.parents:  # belt and braces behind the key pattern
            raise InvalidBlobKey("blob key escapes the store")
        return path

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        if path.exists() and path.read_bytes() == data:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(temp, path)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise

    def get(self, key: str) -> bytes | None:
        path = self._path(key)
        return path.read_bytes() if path.is_file() else None
