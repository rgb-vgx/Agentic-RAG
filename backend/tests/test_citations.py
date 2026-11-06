"""Citation validation — deterministic backend truth (Section 7.5)."""

from app.agentic_rag.validation.citations import CitationValidator
from app.domain.retrieval.types import EvidenceGroup, RetrievedEvidence
from uuid import uuid4


def make_evidence(reference_id: str) -> RetrievedEvidence:
    return RetrievedEvidence(
        chunk_id=uuid4(),
        document_id=uuid4(),
        content=f"content for {reference_id}",
        score=0.9,
        page_start=1,
        page_end=2,
        heading_path=["Policy", "Leave"],
        reference_id=reference_id,
    )


def make_group(*reference_ids: str) -> EvidenceGroup:
    return EvidenceGroup(
        task_id="t1",
        query="q",
        evidence=[make_evidence(r) for r in reference_ids],
    )


def test_valid_citations_kept_and_mapped():
    answer = "Employees get 12 days [R1] and managers approve [R2]."
    cleaned, sources, invalid = CitationValidator().validate(
        answer, [make_group("R1", "R2")]
    )
    assert invalid == []
    assert "[R1]" in cleaned and "[R2]" in cleaned
    assert [s.reference_id for s in sources] == ["R1", "R2"]
    assert sources[0].page_start == 1


def test_invalid_citation_stripped():
    answer = "Claim [R1] and hallucinated claim [R99]."
    cleaned, sources, invalid = CitationValidator().validate(
        answer, [make_group("R1")]
    )
    assert invalid == ["R99"]
    assert "[R99]" not in cleaned
    assert "[R1]" in cleaned
    assert [s.reference_id for s in sources] == ["R1"]


def test_citations_deduped_in_source_order():
    answer = "First [R2], then [R1], again [R2]."
    _, sources, _ = CitationValidator().validate(
        answer, [make_group("R1", "R2")]
    )
    assert [s.reference_id for s in sources] == ["R2", "R1"]


def test_no_citations_returns_no_sources():
    answer = "No claims here."
    cleaned, sources, invalid = CitationValidator().validate(
        answer, [make_group("R1")]
    )
    assert sources == []
    assert invalid == []
    assert cleaned == answer
