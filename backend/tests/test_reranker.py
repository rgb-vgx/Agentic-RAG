"""Reranker client parsing and RerankTool fallback behaviour."""

from uuid import uuid4

from app.agentic_rag.retrieval.tools import RerankTool
from app.domain.retrieval.types import RetrievedEvidence
from app.infrastructure.rerankers.service import HttpReranker


def _evidence(count: int) -> list[RetrievedEvidence]:
    return [
        RetrievedEvidence(
            chunk_id=f"c{i}",
            document_id=uuid4(),
            content=f"document {i}",
            score=0.0,
        )
        for i in range(count)
    ]


def test_parse_scores_maps_by_index():
    body = {
        "results": [
            {"index": 2, "relevance_score": 0.9},
            {"index": 0, "relevance_score": 0.1},
        ]
    }
    assert HttpReranker._parse_scores(body, 3) == [0.1, 0.0, 0.9]


def test_parse_scores_accepts_score_field():
    body = {"results": [{"index": 0, "score": 0.4}]}
    assert HttpReranker._parse_scores(body, 2) == [0.4, 0.0]


def test_parse_scores_empty_response_is_zero():
    assert HttpReranker._parse_scores({}, 2) == [0.0, 0.0]


async def test_rerank_tool_reorders_by_score():
    class FakeReranker:
        async def rerank(self, query, documents):
            return [0.1, 0.9, 0.5]

    result = await RerankTool(FakeReranker()).run("q", _evidence(3), top_k=2)
    assert [item.chunk_id for item in result] == ["c1", "c2"]


async def test_rerank_tool_falls_back_when_reranker_fails():
    class BrokenReranker:
        async def rerank(self, query, documents):
            raise RuntimeError("reranker down")

    result = await RerankTool(BrokenReranker()).run(
        "q", _evidence(3), top_k=2
    )
    assert [item.chunk_id for item in result] == ["c0", "c1"]
