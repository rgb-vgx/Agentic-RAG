"""Deterministic fakes for ChatService/graph tests (no network, no keys)."""

from typing import Any

from app.agentic_rag.graph.nodes import NodeContext
from app.domain.agents.state import WorkflowLimits
from app.infrastructure.llm.base import LLMResponse

ANSWER = "Fake answer."


class FakeLLM:
    async def complete(self, messages, **kwargs) -> LLMResponse:
        return LLMResponse(content=ANSWER, model="fake", provider="fake")

    async def complete_json(self, messages, **kwargs) -> dict[str, Any]:
        return {
            "standalone_query": "q",
            "query_type": "simple_fact",
            "complexity": "low",
            "entities": [],
            "is_follow_up": False,
            "status": "SUFFICIENT",
        }

    async def stream(self, messages, **kwargs):
        yield ANSWER


class FakeRetriever:
    async def hybrid_search(self, **kwargs) -> list:
        return []

    @staticmethod
    def to_evidence(hit: Any, reference_id: str = "") -> Any:
        raise AssertionError("no hits expected")


class FakeEmbedder:
    async def embed_query(self, text: str) -> list[float]:
        return [0.0] * 8


def fake_node_context() -> NodeContext:
    return NodeContext(
        llm=FakeLLM(),  # type: ignore[arg-type]
        retriever=FakeRetriever(),  # type: ignore[arg-type]
        embedder=FakeEmbedder(),
        limits=WorkflowLimits(max_retrieval_iterations=1, max_decomposition_tasks=1),
    )
