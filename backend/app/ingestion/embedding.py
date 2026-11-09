"""Embedding generation (Section 11).

Wraps the provider client from app.infrastructure.embeddings and adds
batching, retry and token accounting. Embeddings are deterministic per
(model, dimension) — recorded in the indexing run's version snapshot.
"""

import asyncio

from app.domain.documents.entities import Document
from app.ingestion.chunking.types import Chunk
from app.ingestion.legal_metadata import extract_article


class EmbeddingPipeline:
    """Turns chunks into vectors via the configured embedding provider."""

    def __init__(self, provider, batch_size: int = 16, max_attempts: int = 3):
        self.provider = provider
        self.batch_size = batch_size
        self.max_attempts = max_attempts

    async def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        """Retry transient provider failures (timeouts, 5xx) with backoff."""
        last_error: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                return await self.provider.embed_documents(batch)
            except Exception as exc:  # provider/SDK errors are transient here
                last_error = exc
                if attempt == self.max_attempts - 1:
                    raise
                await asyncio.sleep(2**attempt)
        raise last_error  # pragma: no cover

    async def embed_chunks(
        self, chunks: list[Chunk], document: Document
    ) -> list[dict]:
        """Return [{chunk, vector}] pairs ready for indexing.

        Metadata needed at query time is copied onto the vector payload:
        tenant_id, knowledge_base_id, document_id, chunk_set_id, active.
        """
        texts = [self._embed_text(chunk) for chunk in chunks]
        vectors: list[list[float]] = []

        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            vectors.extend(await self._embed_batch(batch))

        return [
            {
                "chunk": chunk,
                "vector": vector,
                "payload": self._payload(chunk, document),
            }
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]

    async def embed_query(self, text: str) -> list[float]:
        return await self.provider.embed_query(text)

    @staticmethod
    def _embed_text(chunk: Chunk) -> str:
        """Chunk text with heading context — matches how it was indexed."""
        if chunk.heading_path and "[Section:" not in chunk.content[:60]:
            prefix = " > ".join(chunk.heading_path)
            return f"[Section: {prefix}]\n\n{chunk.content}"
        return chunk.content

    @staticmethod
    def _payload(chunk: Chunk, document: Document) -> dict:
        payload = {
            "tenant_id": document.tenant_id,
            "knowledge_base_id": document.knowledge_base_id,
            "document_id": str(document.id),
            "chunk_id": chunk.chunk_id,
            "section_id": chunk.section_id,
            "page_start": chunk.page_start,
            "page_end": chunk.page_end,
            "heading_path": chunk.heading_path,
            "strategy_name": chunk.strategy_name,
            "strategy_version": chunk.strategy_version,
            "chunk_index": chunk.chunk_index,
            "parent_chunk_id": chunk.parent_chunk_id,
            "active": True,
            "document_title": document.title,
            "document_number": document.document_number,
            "document_year": document.document_year,
            "document_type": (document.run_metadata or {}).get("document_type"),
            "metadata_version": 1,
        }
        article_number, article_title = extract_article(chunk.heading_path)
        if article_number:
            payload["article_number"] = article_number
            payload["article_title"] = article_title
            payload["legal_unit_id"] = (
                f"{document.id}:article:{article_number}"
            )
        return payload
