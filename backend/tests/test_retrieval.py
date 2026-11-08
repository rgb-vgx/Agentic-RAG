"""Sparse encoder + RRF fusion + evidence evaluation fallbacks."""

from app.agentic_rag.evaluation.evidence import EvidenceEvaluator
from app.domain.retrieval.types import EvidenceGroup, EvidenceStatus
from app.infrastructure.vectorstores.qdrant_store import HybridRetriever, RawHit
from app.infrastructure.vectorstores.sparse import SparseEncoder
from uuid import uuid4


# ---------------------------------------------------------------------------
# Sparse encoder
# ---------------------------------------------------------------------------


def test_sparse_encode_produces_aligned_indices_values():
    indices, values = SparseEncoder().encode("vacation policy vacation days")
    assert len(indices) == len(values)
    assert len(set(indices)) == len(indices)  # no duplicate terms
    assert all(v > 0 for v in values)


def test_sparse_encode_empty_text():
    indices, values = SparseEncoder().encode("")
    assert indices == [] and values == []


def test_sparse_encode_is_deterministic():
    encoder = SparseEncoder()
    assert encoder.encode("leave policy") == encoder.encode("leave policy")


# ---------------------------------------------------------------------------
# RRF fusion
# ---------------------------------------------------------------------------


def make_hit(chunk_id: str, score: float, dense_rank: int = -1, sparse_rank: int = -1):
    return RawHit(
        chunk_id=chunk_id,
        document_id="doc",
        score=score,
        payload={"chunk_id": chunk_id},
        dense_rank=dense_rank,
        sparse_rank=sparse_rank,
    )


def test_rrf_fusion_combines_channels():
    retriever = HybridRetriever(client=None, collection="c", dense_dim=8)  # type: ignore[arg-type]
    dense = [make_hit("a", 0.9), make_hit("b", 0.8)]
    sparse = [make_hit("b", 0.7), make_hit("c", 0.6)]
    fused = retriever.rrf_fuse(dense, sparse)
    ids = [h.chunk_id for h in fused]
    # "b" appears in both channels → should rank first
    assert ids[0] == "b"
    assert set(ids) == {"a", "b", "c"}


def test_rrf_single_channel_preserves_order():
    retriever = HybridRetriever(client=None, collection="c", dense_dim=8)  # type: ignore[arg-type]
    dense = [make_hit("x", 0.9), make_hit("y", 0.5)]
    fused = retriever.rrf_fuse(dense, [])
    assert [h.chunk_id for h in fused] == ["x", "y"]


# ---------------------------------------------------------------------------
# Evidence evaluator fallback (no LLM)
# ---------------------------------------------------------------------------


class FakeLLM:
    async def complete_json(self, *args, **kwargs):
        raise ValueError("no json")


async def test_evaluator_empty_group_is_insufficient():
    evaluator = EvidenceEvaluator(FakeLLM())  # type: ignore[arg-type]
    result = await evaluator.evaluate(
        EvidenceGroup(task_id="t1", query="q", evidence=[])
    )
    assert result.status == EvidenceStatus.INSUFFICIENT
    assert result.missing_information


async def test_evaluator_llm_failure_falls_back_to_partial():
    evaluator = EvidenceEvaluator(FakeLLM())  # type: ignore[arg-type]
    evidence_group = EvidenceGroup(
        task_id="t1",
        query="q",
        evidence=[
            # minimal evidence so the non-empty path runs
        ],
    )
    # Non-empty path with a broken LLM
    from app.domain.retrieval.types import RetrievedEvidence

    evidence_group.evidence.append(
        RetrievedEvidence(
            chunk_id=str(uuid4()) + "-s0-c1",
            document_id=uuid4(),
            content="content",
            score=0.5,
            reference_id="R1",
        )
    )
    result = await evaluator.evaluate(evidence_group)
    assert result.status == EvidenceStatus.PARTIALLY_SUFFICIENT


def test_sparse_encode_keeps_vietnamese_words_whole():
    indices, values = SparseEncoder().encode("nghỉ phép năm")
    assert len(indices) == len(values)
    # three words, each indexed with its accent-stripped variant
    assert len(indices) == 6


def test_sparse_encode_is_diacritic_insensitive():
    with_marks = set(SparseEncoder().encode("nghỉ phép năm")[0])
    without_marks = set(SparseEncoder().encode("nghi phep nam")[0])
    # the unaccented forms are present in both vocabularies
    assert len(with_marks & without_marks) == 3


def test_sparse_encode_handles_d_stroke():
    with_d = set(SparseEncoder().encode("đường")[0])
    without_d = set(SparseEncoder().encode("duong")[0])
    assert with_d & without_d
