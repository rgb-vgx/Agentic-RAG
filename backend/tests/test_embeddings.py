"""Embedding provider: query instruction applies to queries, not documents."""

from types import SimpleNamespace

from app.config import settings
from app.infrastructure import embeddings


class FakeEmbeddings:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.embeddings = self  # mimic client.embeddings.create

    async def create(self, model: str, input: list[str]):
        self.calls.append(input)
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=[0.0]) for _ in input]
        )


async def test_instruction_applies_to_queries_only(monkeypatch):
    monkeypatch.setattr(
        settings, "embedding_query_instruction", "Given a question, retrieve it"
    )
    provider = embeddings.OpenAIEmbeddingProvider(api_key="test")
    fake = FakeEmbeddings()
    provider._client = fake  # type: ignore[assignment]

    await provider.embed_query("câu hỏi")
    await provider.embed_documents(["tài liệu"])

    assert fake.calls[0] == [
        "Instruct: Given a question, retrieve it\nQuery: câu hỏi"
    ]
    assert fake.calls[1] == ["tài liệu"]
