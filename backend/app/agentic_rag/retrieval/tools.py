"""Retrieval tools (Section 7.2) — the orchestrator's toolkit.

Each tool returns RetrievedEvidence (the output contract in 7.2) and is
tenant-scoped. Tools are invoked by the retrieval node, not by the LLM
directly — the orchestrator controls which tool runs (centralized control,
Section 6.1).
"""

from app.domain.retrieval.types import RetrievedEvidence
from app.infrastructure.vectorstores.qdrant_store import HybridRetriever
from app.infrastructure.vectorstores.sparse import SparseEncoder


class HybridRetrievalTool:
    """Dense + sparse hybrid search with RRF fusion."""

    name = "hybrid_retrieval"

    def __init__(self, retriever: HybridRetriever, encoder: SparseEncoder | None = None):
        self.retriever = retriever
        self.encoder = encoder or SparseEncoder()

    async def run(
        self,
        query: str,
        tenant_id: str,
        knowledge_base_id: str | None = None,
        document_ids: list[str] | None = None,
        limit: int = 20,
        query_vector: list[float] | None = None,
    ) -> list[RetrievedEvidence]:
        dense = query_vector
        if dense is None:
            # Caller passes the embedded query; encode lazily if not provided
            raise ValueError("query_vector is required — embed before calling")

        sparse_indices, sparse_values = self.encoder.encode(query)
        hits = await self.retriever.hybrid_search(
            dense_vector=dense,
            sparse_indices=sparse_indices,
            sparse_values=sparse_values,
            tenant_id=tenant_id,
            limit=limit,
            knowledge_base_id=knowledge_base_id,
            document_ids=document_ids,
        )
        return [
            self.retriever.to_evidence(hit, reference_id=f"R{i + 1}")
            for i, hit in enumerate(hits)
        ]


class ContextExpansionTool:
    """Expand a matched small chunk to its parent window (Section 11.3)."""

    name = "context_expansion"

    def __init__(self, retriever: HybridRetriever):
        self.retriever = retriever

    async def run(
        self, evidence: RetrievedEvidence, tenant_id: str, limit: int = 5
    ) -> list[RetrievedEvidence]:
        parent_id = evidence.metadata.get("parent_chunk_id")
        if not parent_id:
            return []

        # Parent point is indexed in the same collection under its own id
        try:
            points = await self.retriever.client.retrieve(
                collection_name=self.retriever.collection,
                ids=[HybridRetriever_point_id(evidence.document_id, parent_id)],
                with_payload=True,
            )
        except Exception:
            return []

        results: list[RetrievedEvidence] = []
        for point in points:
            payload = point.payload or {}
            if payload.get("tenant_id") != tenant_id or not payload.get("active", True):
                continue
            results.append(
                RetrievedEvidence(
                    chunk_id=payload.get("chunk_id", parent_id),
                    document_id=payload.get("document_id", evidence.document_id),
                    content=payload.get("content", ""),
                    score=evidence.score,
                    page_start=payload.get("page_start"),
                    page_end=payload.get("page_end"),
                    heading_path=payload.get("heading_path", []),
                    metadata={"expanded_from": evidence.chunk_id},
                    reference_id=evidence.reference_id,
                )
            )
        return results[:limit]


def HybridRetriever_point_id(document_id: str, chunk_id: str) -> str:
    from app.ingestion.indexing import QdrantIndexer

    return QdrantIndexer.point_id(document_id, chunk_id)


class RerankTool:
    """Cross-encoder / API reranking (Section 11.2).

    Provider-agnostic: any reranker exposing score(query, documents) fits.
    When no reranker is configured the original order is preserved.
    """

    name = "rerank"

    def __init__(self, reranker=None):
        self.reranker = reranker

    async def run(
        self, query: str, evidence: list[RetrievedEvidence], top_k: int = 8
    ) -> list[RetrievedEvidence]:
        if self.reranker is None or not evidence:
            return evidence[:top_k]
        try:
            scored = await self.reranker.rerank(
                query, [e.content for e in evidence]
            )
        except Exception as exc:  # reranker down → keep retrieval order
            import logging

            logging.getLogger(__name__).warning(
                "reranker failed, falling back to retrieval order: %s", exc
            )
            return evidence[:top_k]
        if len(scored) != len(evidence):
            return evidence[:top_k]
        reranked = []
        for index in sorted(range(len(evidence)), key=lambda i: scored[i], reverse=True):
            item = evidence[index]
            item.score = scored[index]
            reranked.append(item)
        return reranked[:top_k]
