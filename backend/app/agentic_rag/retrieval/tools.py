"""Retrieval tools (Section 7.2) — the orchestrator's toolkit.

Each tool returns RetrievedEvidence (the output contract in 7.2) and is
tenant-scoped. Tools are invoked by the retrieval node, not by the LLM
directly — the orchestrator controls which tool runs (centralized control,
Section 6.1).
"""

from app.domain.retrieval.types import RetrievedEvidence
from app.infrastructure.vectorstores.qdrant_store import HybridRetriever
from app.infrastructure.vectorstores.sparse import SparseEncoder
from app.ingestion.legal_metadata import LegalReference


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


class ExactLegalReferenceTool:
    """Fetch a referenced Điều directly by metadata, bypassing similarity.

    The article's chunks are concatenated in source order so synthesis sees
    the whole article even when chunking split it; exact candidates are never
    truncated to the semantic top-K.
    """

    name = "exact_legal_reference"

    def __init__(self, retriever: HybridRetriever):
        self.retriever = retriever

    async def run(
        self,
        reference: LegalReference,
        tenant_id: str,
        documents: list[dict] | None = None,
        document_ids: list[str] | None = None,
        resolved_document_id: str | None = None,
        limit: int = 200,
    ) -> list[RetrievedEvidence]:
        from qdrant_client.models import (
            FieldCondition,
            Filter,
            MatchAny,
            MatchValue,
        )

        if not reference.article_number:
            return []

        must = [
            FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
            FieldCondition(key="active", match=MatchValue(value=True)),
            FieldCondition(
                key="article_number",
                match=MatchValue(value=reference.article_number),
            ),
        ]
        if resolved_document_id:
            must.append(
                FieldCondition(
                    key="document_id",
                    match=MatchValue(value=resolved_document_id),
                )
            )
        elif document_ids:
            must.append(
                FieldCondition(key="document_id", match=MatchAny(any=document_ids))
            )

        records, _ = await self.retriever.client.scroll(
            collection_name=self.retriever.collection,
            scroll_filter=Filter(must=must),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        if not records:
            return []

        by_document: dict[str, list] = {}
        for record in records:
            payload = record.payload or {}
            by_document.setdefault(str(payload.get("document_id", "")), []).append(
                payload
            )

        titles = {
            str(doc.get("id")): doc.get("title") or doc.get("filename") or ""
            for doc in (documents or [])
        }

        evidence: list[RetrievedEvidence] = []
        for document_id, payloads in by_document.items():
            payloads.sort(
                key=lambda item: (
                    item.get("chunk_index") or 0,
                    item.get("page_start") or 0,
                )
            )
            content = "\n\n".join(
                str(item.get("content") or "").strip()
                for item in payloads
                if item.get("content")
            ).strip()
            if not content:
                continue
            first = payloads[0]
            heading_path = list(first.get("heading_path") or [])
            document_title = titles.get(document_id) or first.get("document_title")
            if document_title and len(by_document) > 1:
                # Ambiguous reference: keep the document visible in citation.
                heading_path = [str(document_title), *heading_path]
            pages = [
                int(item["page_start"])
                for item in payloads
                if item.get("page_start") is not None
            ]
            evidence.append(
                RetrievedEvidence(
                    chunk_id=str(first.get("chunk_id") or ""),
                    document_id=document_id,
                    content=content,
                    score=1.0,
                    page_start=min(pages) if pages else None,
                    page_end=max(pages) if pages else None,
                    heading_path=heading_path,
                    metadata={
                        "retrieval_method": "exact_legal_reference",
                        "article_number": reference.article_number,
                        "article_title": first.get("article_title"),
                        "document_title": document_title,
                        "source_chunk_ids": [
                            str(item.get("chunk_id") or "") for item in payloads
                        ],
                    },
                )
            )
        return evidence


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
            # The embedding text already carries the section path; give the
            # cross-encoder the same context ("Điều 22 ... > Khoản 1").
            documents = [
                (
                    f"{' > '.join(e.heading_path)}\n{e.content}"
                    if e.heading_path
                    else e.content
                )
                for e in evidence
            ]
            scored = await self.reranker.rerank(query, documents)
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
