"""Embedding provider abstraction (Section 11).

Provider-agnostic interface used by the ingestion pipeline and the query
retriever. Model + dimension are recorded in the version snapshot (9.7) —
changing either requires re-indexing under a new chunk-set version.
"""

from abc import ABC, abstractmethod

from app.config import settings


class BaseEmbeddingProvider(ABC):
    model: str = ""
    dimension: int = 0

    @abstractmethod
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        ...

    @abstractmethod
    async def embed_query(self, text: str) -> list[float]:
        ...


class OpenAIEmbeddingProvider(BaseEmbeddingProvider):
    def __init__(self, api_key: str | None = None, model: str | None = None):
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key or settings.openai_api_key)
        self.model = model or settings.embedding_model
        self.dimension = settings.embedding_dim

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        response = await self._client.embeddings.create(model=self.model, input=texts)
        return [item.embedding for item in response.data]

    async def embed_query(self, text: str) -> list[float]:
        response = await self._client.embeddings.create(model=self.model, input=[text])
        return response.data[0].embedding


class LocalHashEmbeddingProvider(BaseEmbeddingProvider):
    """Deterministic offline fallback for tests — not for production quality."""

    def __init__(self, dimension: int = 256):
        self.model = "local-hash"
        self.dimension = dimension

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        import hashlib

        vector = [0.0] * self.dimension
        for token in text.lower().split():
            digest = hashlib.md5(token.encode()).digest()
            index = int.from_bytes(digest[:4], "little") % self.dimension
            vector[index] += 1.0
        norm = sum(v * v for v in vector) ** 0.5 or 1.0
        return [v / norm for v in vector]


def get_embedding_provider(provider: str | None = None) -> BaseEmbeddingProvider:
    name = (provider or settings.embedding_provider).lower()
    if name == "openai":
        return OpenAIEmbeddingProvider()
    if name == "local":
        return LocalHashEmbeddingProvider()
    raise ValueError(f"Unknown embedding provider: {name}")
