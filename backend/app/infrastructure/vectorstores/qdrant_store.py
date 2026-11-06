"""Qdrant vector store — query side (Section 11.1/11.3).

Hybrid retrieval: dense + sparse search, Reciprocal Rank Fusion (RRF),
then optional reranking. All queries are tenant-filtered server-side —
client-side filtering is never the only isolation boundary (Section 15.2).
"""

from dataclasses import dataclass, field
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue, SparseVector

from app.domain.retrieval.types import RetrievedEvidence


@dataclass
class RawHit:
    chunk_id: str
    document_id: str
    score: float
    payload: dict[str, Any] = field(default_factory=dict)
    dense_rank: int = -1
    sparse_rank: int = -1


class HybridRetriever:
    """Dense + sparse retrieval with RRF fusion."""

    RRF_K = 60  # standard RRF constant

    def __init__(
        self,
        client: QdrantClient,
        collection: str,
        dense_dim: int,
        enable_sparse: bool = True,
    ):
        self.client = client
        self.collection = collection
        self.dense_dim = dense_dim
        self.enable_sparse = enable_sparse

    # ------------------------------------------------------------------
    # Single-channel searches
    # ------------------------------------------------------------------

    def _base_filter(
        self,
        tenant_id: str,
        knowledge_base_id: str | None = None,
        document_ids: list[str] | None = None,
        active_only: bool = True,
        chunk_set_ids: list[str] | None = None,
    ) -> Filter:
        must: list[FieldCondition] = [
            FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
        ]
        if active_only:
            must.append(
                FieldCondition(key="active", match=MatchValue(value=True))
            )
        if knowledge_base_id:
            must.append(
                FieldCondition(
                    key="knowledge_base_id", match=MatchValue(value=knowledge_base_id)
                )
            )
        if document_ids:
            must.append(
                FieldCondition(key="document_id", match=MatchAny(any=document_ids))
            )
        if chunk_set_ids:
            must.append(
                FieldCondition(key="chunk_set_id", match=MatchAny(any=chunk_set_ids))
            )
        return Filter(must=must)

    def search_dense(
        self,
        vector: list[float],
        tenant_id: str,
        limit: int = 20,
        **filter_kwargs,
    ) -> list[RawHit]:
        hits = self.client.query_points(
            collection_name=self.collection,
            query=vector,
            using="dense",
            limit=limit,
            query_filter=self._base_filter(tenant_id, **filter_kwargs),
            with_payload=True,
        ).points
        return [
            RawHit(
                chunk_id=str(hit.payload.get("chunk_id", hit.id)),
                document_id=str(hit.payload.get("document_id", "")),
                score=hit.score,
                payload=hit.payload or {},
                dense_rank=index,
            )
            for index, hit in enumerate(hits)
        ]

    def search_sparse(
        self,
        indices: list[int],
        values: list[float],
        tenant_id: str,
        limit: int = 20,
        **filter_kwargs,
    ) -> list[RawHit]:
        if not self.enable_sparse or not indices:
            return []
        hits = self.client.query_points(
            collection_name=self.collection,
            query=SparseVector(indices=indices, values=values),
            using="sparse",
            limit=limit,
            query_filter=self._base_filter(tenant_id, **filter_kwargs),
            with_payload=True,
        ).points
        return [
            RawHit(
                chunk_id=str(hit.payload.get("chunk_id", hit.id)),
                document_id=str(hit.payload.get("document_id", "")),
                score=hit.score,
                payload=hit.payload or {},
                sparse_rank=index,
            )
            for index, hit in enumerate(hits)
        ]

    # ------------------------------------------------------------------
    # Fusion
    # ------------------------------------------------------------------

    def rrf_fuse(
        self, dense_hits: list[RawHit], sparse_hits: list[RawHit]
    ) -> list[RawHit]:
        """Reciprocal Rank Fusion across dense and sparse channels.

        Only rank-based contributions count — raw similarity scores from the
        two channels are on different scales and must not be mixed in.
        """
        fused: dict[str, RawHit] = {}

        for rank, hit in enumerate(dense_hits, start=1):
            key = hit.chunk_id
            if key not in fused:
                hit.score = 0.0  # start from pure RRF, drop raw similarity
                fused[key] = hit
            fused[key].score += 1.0 / (self.RRF_K + rank)
            fused[key].dense_rank = rank

        for rank, hit in enumerate(sparse_hits, start=1):
            key = hit.chunk_id
            if key in fused:
                fused[key].score += 1.0 / (self.RRF_K + rank)
                fused[key].sparse_rank = rank
            else:
                hit.score = 1.0 / (self.RRF_K + rank)
                hit.sparse_rank = rank
                fused[key] = hit

        return sorted(fused.values(), key=lambda h: h.score, reverse=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def hybrid_search(
        self,
        dense_vector: list[float],
        sparse_indices: list[int] | None,
        sparse_values: list[float] | None,
        tenant_id: str,
        limit: int = 20,
        **filter_kwargs,
    ) -> list[RawHit]:
        dense_hits = self.search_dense(
            dense_vector, tenant_id, limit=limit * 2, **filter_kwargs
        )
        sparse_hits = (
            self.search_sparse(
                sparse_indices or [],
                sparse_values or [],
                tenant_id,
                limit=limit * 2,
                **filter_kwargs,
            )
            if sparse_indices
            else []
        )
        fused = self.rrf_fuse(dense_hits, sparse_hits)
        return fused[:limit]

    @staticmethod
    def to_evidence(hit: RawHit, reference_id: str = "") -> RetrievedEvidence:
        payload = hit.payload
        return RetrievedEvidence(
            chunk_id=payload.get("chunk_id", hit.chunk_id),
            document_id=payload.get("document_id", hit.document_id),
            content=payload.get("content", ""),
            score=hit.score,
            page_start=payload.get("page_start"),
            page_end=payload.get("page_end"),
            heading_path=payload.get("heading_path", []),
            metadata={
                "dense_rank": hit.dense_rank,
                "sparse_rank": hit.sparse_rank,
                "section_id": payload.get("section_id"),
                "strategy_name": payload.get("strategy_name"),
                "parent_chunk_id": payload.get("parent_chunk_id"),
            },
            reference_id=reference_id,
        )
