"""Citation validation — marker validity + claim support (Section 7.5)."""

from uuid import uuid4

from app.agentic_rag.validation.citations import CitationValidator
from app.domain.retrieval.types import EvidenceGroup, RetrievedEvidence


def make_evidence(reference_id: str, content: str = "") -> RetrievedEvidence:
    return RetrievedEvidence(
        chunk_id=uuid4(),
        document_id=uuid4(),
        content=content or f"content for {reference_id}",
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


# ---------------------------------------------------------------------------
# Marker validity
# ---------------------------------------------------------------------------


def test_valid_citations_kept_and_mapped():
    answer = "Employees get 12 days [R1] and managers approve [R2]."
    result = CitationValidator().validate(answer, [make_group("R1", "R2")])
    assert result.invalid_references == []
    assert "[R1]" in result.answer and "[R2]" in result.answer
    assert [s.reference_id for s in result.sources] == ["R1", "R2"]
    assert result.sources[0].page_start == 1
    assert result.fallback is False


def test_invalid_citation_stripped():
    answer = "Managers must approve requests in advance [R99]."
    result = CitationValidator().validate(answer, [make_group("R1")])
    assert result.invalid_references == ["R99"]
    assert "[R99]" not in result.answer


def test_citations_deduped_in_source_order():
    answer = "First [R2], then [R1], again [R2]."
    result = CitationValidator().validate(answer, [make_group("R1", "R2")])
    assert [s.reference_id for s in result.sources] == ["R2", "R1"]


# ---------------------------------------------------------------------------
# Claim support — stripping a marker must not leave the claim standing
# ---------------------------------------------------------------------------


def test_uncited_numeric_claim_is_dropped():
    answer = "Nhân viên được nghỉ 30 ngày mỗi năm."
    result = CitationValidator().validate(answer, [make_group("R1")], user_query="Nghỉ phép?")
    # The claim had no citation: it must not survive as an uncited assertion
    assert "30 ngày" not in result.answer
    assert result.fallback is True
    assert "đủ bằng chứng" in result.answer  # Vietnamese query → Vietnamese fallback


def test_uncited_claim_with_valid_sibling_keeps_the_cited_one():
    answer = "Managers approve requests [R1]. Probation staff get 30 days [R99]."
    result = CitationValidator().validate(answer, [make_group("R1")])
    assert result.invalid_references == ["R99"]
    assert "[R1]" in result.answer
    assert "Managers approve requests" in result.answer
    # The [R99] claim is gone after stripping
    assert "30 days" not in result.answer
    assert result.fallback is False  # supported content remains


def test_answer_without_citations_and_without_evidence_is_not_fallback():
    answer = "No relevant information was found in the documents."
    result = CitationValidator().validate(answer, [], user_query="What is it?")
    assert result.fallback is False  # no evidence existed → no grounding to lose
    assert result.answer == answer


def test_english_query_gets_english_fallback():
    answer = "Employees get 30 days per year."
    result = CitationValidator().validate(
        answer, [make_group("R1")], user_query="How many vacation days?"
    )
    assert result.fallback is True
    assert "evidence" in result.answer


def test_cited_claim_survives_validation():
    answer = "Employees get 12 days per year [R1]."
    result = CitationValidator().validate(answer, [make_group("R1")])
    assert result.fallback is False
    assert "12 days" in result.answer
    assert result.dropped_claims == []
