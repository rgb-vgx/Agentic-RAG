"""Qdrant indexing (Section 9.5): write chunks, manage chunk-set versions.

Indexing rules from the proposal:
  - Every write carries tenant_id / knowledge_base_id / chunk_set_id / active.
  - The active chunk set is versioned; the previous index is never deleted
    before the new version is ready.
  - Point ids are deterministic (uuid5 of document + chunk) so re-indexing
    the same chunk set is idempotent.
"""

import uuid
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

from app.ingestion.chunking.types import Chunk

# Deterministic point ids → idempotent re-indexing
_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


class QdrantIndexer:
    """Writes dense (+ sparse when configured) vectors to Qdrant."""

    def __init__(
        self,
        client: QdrantClient,
        collection: str,
        dense_dim: int,
        enable_sparse: bool = True,
        upsert_batch_size: int = 128,
    ):
        self.client = client
        self.collection = collection
        self.dense_dim = dense_dim
        self.enable_sparse = enable_sparse
        self.upsert_batch_size = upsert_batch_size

    # ------------------------------------------------------------------
    # Collection management
    # ------------------------------------------------------------------

    def ensure_collection(self) -> None:
        if self.client.collection_exists(self.collection):
            return

        vectors: dict = {
            "dense": VectorParams(size=self.dense_dim, distance=Distance.COSINE),
        }
        sparse_vectors: dict | None = None
        if self.enable_sparse:
            sparse_vectors = {
                "sparse": SparseVectorParams(
                    index=self._sparse_index_params(),
                )
            }

        self.client.create_collection(
            collection_name=self.collection,
            vectors_config=vectors,
            sparse_vectors_config=sparse_vectors,
        )
        self._ensure_payload_indexes()

    @staticmethod
    def _sparse_index_params():
        from qdrant_client.models import SparseIndexParams

        return SparseIndexParams(on_disk=False)

    def _ensure_payload_indexes(self) -> None:
        """Payload indexes enable tenant/ACL filtering at query time."""
        for field in (
            "tenant_id",
            "knowledge_base_id",
            "document_id",
            "chunk_set_id",
            "active",
            "article_number",
            "document_number",
            "legal_unit_id",
        ):
            try:
                self.client.create_payload_index(
                    collection_name=self.collection,
                    field_name=field,
                    field_schema="keyword",
                )
            except Exception:
                pass  # index already exists

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    @staticmethod
    def point_id(document_id: str, chunk_id: str) -> str:
        return str(uuid.uuid5(_NAMESPACE, f"{document_id}:{chunk_id}"))

    def upsert_chunks(
        self,
        items: list[dict[str, Any]],
        chunk_set_id: str,
        sparse_vectors: dict[str, tuple[list[int], list[float]]] | None = None,
    ) -> int:
        """Upsert embedded chunks. `items` comes from EmbeddingPipeline."""
        points: list[PointStruct] = []
        for index, item in enumerate(items):
            chunk: Chunk = item["chunk"]
            payload = dict(item["payload"])
            payload["chunk_set_id"] = chunk_set_id
            payload["content"] = chunk.content

            vectors: dict[str, Any] = {"dense": item["vector"]}
            if sparse_vectors and chunk.chunk_id in sparse_vectors:
                indices, values = sparse_vectors[chunk.chunk_id]
                vectors["sparse"] = SparseVector(indices=indices, values=values)

            points.append(
                PointStruct(
                    id=self.point_id(chunk.document_id, chunk.chunk_id),
                    vector=vectors,
                    payload=payload,
                )
            )

        # Batch the upsert: 845 points × 4096 dims in one request is a
        # multi-megabyte payload that exceeds the Qdrant HTTP timeout.
        written = 0
        for start in range(0, len(points), self.upsert_batch_size):
            batch = points[start : start + self.upsert_batch_size]
            self.client.upsert(
                collection_name=self.collection, points=batch, wait=True
            )
            written += len(batch)
        return written

    def deactivate_chunk_set(self, chunk_set_id: str, tenant_id: str) -> None:
        """Mark an old chunk set inactive instead of deleting it.

        The system must not delete the currently active index before a new
        indexing version is ready (Section 9.5) — soft deactivate first,
        hard delete runs in a later maintenance job.
        """
        self.client.set_payload(
            collection_name=self.collection,
            payload={"active": False},
            points=self._chunk_set_points(chunk_set_id, tenant_id),
        )

    def delete_chunk_set(self, chunk_set_id: str, tenant_id: str) -> None:
        self.client.delete(
            collection_name=self.collection,
            points_selector=self._chunk_set_points(chunk_set_id, tenant_id),
        )

    def _chunk_set_points(self, chunk_set_id: str, tenant_id: str) -> list[str]:
        records, _ = self.client.scroll(
            collection_name=self.collection,
            scroll_filter=Filter(
                must=[
                    FieldCondition(key="chunk_set_id", match=MatchValue(value=chunk_set_id)),
                    FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
                ]
            ),
            limit=10_000,
            with_payload=["chunk_id"],
            with_vectors=False,
        )
        return [record.id for record in records]
