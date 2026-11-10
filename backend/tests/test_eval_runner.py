"""Legal eval scoring."""

from evals.runner import score_case


def test_score_requires_expected_citation_and_terms():
    case = {
        "id": "x",
        "expected_documents": ["law.docx"],
        "required_terms": ["thẩm quyền"],
        "max_latency_ms": 1000,
    }
    result = score_case(
        case,
        answer="Tòa án có thẩm quyền [R1].",
        cited_document_ids=["doc-law"],
        document_ids_by_name={"law.docx": "doc-law"},
        latency_ms=500,
    )
    assert result["passed"] is True


def test_score_rejects_fallback_and_wrong_document():
    case = {
        "id": "x",
        "expected_documents": ["law.docx"],
        "required_terms": ["thẩm quyền"],
        "max_latency_ms": 1000,
    }
    result = score_case(
        case,
        answer="Tài liệu không cung cấp đủ bằng chứng.",
        cited_document_ids=[],
        document_ids_by_name={"law.docx": "doc-law"},
        latency_ms=500,
    )
    assert result["passed"] is False
    assert result["fallback"] is True
    assert result["citation_hit"] is False
