"""Factory wiring Qdrant indexer + retriever from settings (Section 16)."""

from functools import lru_cache

from qdrant_client import QdrantClient

from app.config import settings
from app.infrastructure.vectorstores.qdrant_store import HybridRetriever
from app.ingestion.indexing import QdrantIndexer


@lru_cache(maxsize=1)
def get_qdrant_client() -> QdrantClient:
    kwargs: dict = {"url": settings.qdrant_url, "prefer_grpc": False}
    if settings.qdrant_api_key:
        kwargs["api_key"] = settings.qdrant_api_key
    return QdrantClient(**kwargs)


def build_indexer() -> QdrantIndexer:
    return QdrantIndexer(
        client=get_qdrant_client(),
        collection=settings.qdrant_collection,
        dense_dim=settings.embedding_dim,
        enable_sparse=True,
    )


def build_retriever() -> HybridRetriever:
    return HybridRetriever(
        client=get_qdrant_client(),
        collection=settings.qdrant_collection,
        dense_dim=settings.embedding_dim,
        enable_sparse=True,
    )
