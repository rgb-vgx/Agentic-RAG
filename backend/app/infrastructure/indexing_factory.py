"""Factory wiring Qdrant indexer + retriever from settings (Section 16)."""

from functools import lru_cache

from qdrant_client import QdrantClient

from app.config import settings
from app.infrastructure.vectorstores.qdrant_store import HybridRetriever
from app.ingestion.indexing import QdrantIndexer


@lru_cache(maxsize=1)
def get_qdrant_client() -> QdrantClient:
    """Sync client — indexing/worker paths only."""
    kwargs: dict = {
        "url": settings.qdrant_url,
        "prefer_grpc": False,
        "timeout": settings.qdrant_timeout_seconds,
    }
    if settings.qdrant_api_key:
        kwargs["api_key"] = settings.qdrant_api_key
    return QdrantClient(**kwargs)


@lru_cache(maxsize=1)
def get_async_qdrant_client():
    """Async client — FastAPI/LangGraph query path (never blocks the loop)."""
    from qdrant_client import AsyncQdrantClient

    kwargs: dict = {
        "url": settings.qdrant_url,
        "prefer_grpc": False,
        "timeout": settings.qdrant_timeout_seconds,
    }
    if settings.qdrant_api_key:
        kwargs["api_key"] = settings.qdrant_api_key
    return AsyncQdrantClient(**kwargs)


def build_indexer() -> QdrantIndexer:
    return QdrantIndexer(
        client=get_qdrant_client(),
        collection=settings.qdrant_collection,
        dense_dim=settings.embedding_dim,
        enable_sparse=True,
    )


def build_retriever() -> HybridRetriever:
    return HybridRetriever(
        client=get_async_qdrant_client(),
        collection=settings.qdrant_collection,
        dense_dim=settings.embedding_dim,
        enable_sparse=settings.retrieval_enable_sparse,
    )
