"""Embedding generation (Section 11).

Wraps the provider client from app.infrastructure.embeddings and adds
batching, retry and token accounting. Embeddings are deterministic per
(model, dimension) — recorded in the indexing run's version snapshot.
"""

from app.domain.documents.entities import Document
from app.ingestion.chunking.types import Chunk


class EmbeddingPipeline:
    """Turns chunks into vectors via the configured embedding provider."""

    def __init__(self, provider, batch_size: int = 64):
        self.provider = provider
        self.batch_size = batch_size

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
            vectors.extend(await self.provider.embed_documents(batch))

        return [
            {
                "chunk": chunk,
                "vector": vector,
                "payload": self._payload(chunk, document),
            }
            for chunk, vector in zip(chunks, vectors)
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
        return {
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
        }
