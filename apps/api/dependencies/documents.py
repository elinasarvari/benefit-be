from __future__ import annotations

from fastapi import Request

from apps.api.dependencies.db import get_mongo, get_settings
from apps.api.repositories.documents import MongoDocumentRepository
from packages.platform.blobs import BlobStore, LocalBlobStore


def get_document_repository(request: Request) -> MongoDocumentRepository:
    return MongoDocumentRepository(get_mongo(request), get_settings(request).MONGO_DB)


def get_blob_store(request: Request) -> BlobStore:
    """One local store per process (ADR-002-03). Tests replace it with `MemoryBlobStore`."""
    store: BlobStore | None = getattr(request.app.state, "blobs", None)
    if store is None:
        store = LocalBlobStore(get_settings(request).BLOB_DIR)
        request.app.state.blobs = store
    return store
