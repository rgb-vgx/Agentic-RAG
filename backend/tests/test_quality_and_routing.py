"""Document quality assessment gates (Section 4.4) and routing (15.1)."""

from app.agentic_rag.planning import route_path
from app.domain.agents.state import QueryAnalysis
from app.ingestion.analyzer import DocumentAnalyzer
from app.ingestion.ir import BlockType, CanonicalDocument, DocumentBlock


def make_document(blocks: list[DocumentBlock], pages: int = 3) -> CanonicalDocument:
    return CanonicalDocument(
        document_id="doc-1",
        filename="x.pdf",
        file_type="pdf",
        blocks=blocks,
        total_pages=pages,
    )


def block(index: int, text: str, page: int = 1, block_type=BlockType.PARAGRAPH):
    return DocumentBlock(
        block_id=f"b{index}",
        block_type=block_type,
        text=text,
        page_start=page,
        page_end=page,
        block_index=index,
    )


def test_scanned_document_requires_ocr():
    doc = make_document([block(0, "")], pages=5)
    report = DocumentAnalyzer().analyze(doc)
    assert report.requires_ocr is True
    assert report.is_acceptable is False
    assert any("OCR" in w for w in report.warnings)


def test_good_document_passes_gate():
    blocks = [
        block(0, "Vacation Policy", block_type=BlockType.HEADING),
        *[block(i + 1, f"Paragraph number {i} with reasonable body text length.") for i in range(20)],
    ]
    report = DocumentAnalyzer().analyze(make_document(blocks))
    assert report.requires_ocr is False
    assert report.extraction_quality > 0.7
    assert report.requires_manual_review is False


def test_repeating_header_footer_flagged():
    # Same first line on every page
    blocks = []
    index = 0
    for page in range(1, 6):
        blocks.append(block(index, "ACME CORPORATION — CONFIDENTIAL", page=page))
        index += 1
        for _ in range(3):
            blocks.append(
                block(index, f"Body paragraph on page {page} with normal sentence text.", page=page)
            )
            index += 1
    report = DocumentAnalyzer().analyze(make_document(blocks, pages=5))
    assert any("header/footer" in w for w in report.warnings)
    # Header/footer noise is an extraction-quality issue, not a reading-order one
    assert report.extraction_quality < 0.8


# ---------------------------------------------------------------------------
# Complexity routing (Section 15.1)
# ---------------------------------------------------------------------------


def test_route_simple_fact_to_fast():
    assert route_path(QueryAnalysis(query_type="simple_fact", complexity="low")) == "fast"


def test_route_multi_hop_to_agentic():
    assert route_path(QueryAnalysis(query_type="multi_hop", complexity="high")) == "agentic"


def test_route_comparison_to_agentic():
    assert route_path(QueryAnalysis(query_type="comparison", complexity="medium")) == "agentic"


def test_route_medium_to_standard():
    assert route_path(QueryAnalysis(query_type="conditional", complexity="medium")) == "standard"
