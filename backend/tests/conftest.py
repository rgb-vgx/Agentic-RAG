"""Shared fixtures — sample IR sections for chunking tests."""

import pytest

from app.ingestion.ir import BlockType, CanonicalDocument, DocumentBlock, DocumentSection


def make_section(
    texts: list[str],
    block_types: list[BlockType] | None = None,
    title: str = "Section",
    level: int = 1,
) -> DocumentSection:
    block_types = block_types or [BlockType.PARAGRAPH] * len(texts)
    blocks = [
        DocumentBlock(
            block_id=f"b{i}",
            block_type=block_types[i],
            text=text,
            page_start=1,
            page_end=1,
            block_index=i,
            heading_path=[title],
            table_data=(
                [row.split(" | ") for row in text.split("\n")]
                if block_types[i] == BlockType.TABLE
                else None
            ),
        )
        for i, text in enumerate(texts)
    ]
    return DocumentSection(
        section_id="s0", title=title, level=level, blocks=blocks,
        heading_path=[title],
    )


@pytest.fixture
def prose_section() -> DocumentSection:
    paragraphs = [
        "The vacation policy grants employees 12 days of paid leave per year.",
        "Unused leave may be carried over to the next year up to 5 days.",
        "Managers must approve leave requests at least two weeks in advance.",
        "Sick leave is tracked separately and does not affect vacation balance.",
        "Employees hired mid-year receive a prorated allowance based on start date.",
        "Terminal employees receive payout for unused leave at separation.",
        "Public holidays are listed in Appendix A of the employee handbook.",
        "Special unpaid leave requires written approval from HR and the manager.",
    ]
    return make_section(paragraphs, title="Vacation Policy")


@pytest.fixture
def table_section() -> DocumentSection:
    texts = [
        "Leave types and annual allowances are shown below.",
        "Type | Days | Carryover\nVacation | 12 | 5\nSick | 10 | 0",
        "Contact HR for additional details.",
    ]
    types = [
        BlockType.PARAGRAPH,
        BlockType.TABLE,
        BlockType.PARAGRAPH,
    ]
    return make_section(texts, types, title="Allowances")


@pytest.fixture
def sample_document() -> CanonicalDocument:
    return CanonicalDocument(
        document_id="doc-1",
        filename="policy.pdf",
        file_type="pdf",
        total_pages=3,
    )
