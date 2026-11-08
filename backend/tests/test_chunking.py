"""Chunking strategies and constraint-first selection (Section 5)."""

from app.ingestion.chunking.selection import ChunkingSelector, composite_chunk
from app.ingestion.chunking.strategies import (
    STRATEGY_REGISTRY,
    ChunkContext,
    RecursiveCharacterStrategy,
    TableAwareStrategy,
    count_tokens,
)
from app.ingestion.ir import BlockType, DocumentBlock, DocumentSection


def test_registry_has_nine_strategies():
    assert len(STRATEGY_REGISTRY) == 9


def test_recursive_strategy_produces_nonempty_chunks(prose_section):
    ctx = ChunkContext("doc-1", target_tokens=100, overlap=10)
    chunks = RecursiveCharacterStrategy(100, 10).chunk(prose_section, "doc-1", ctx)
    assert chunks
    assert all(c.content.strip() for c in chunks)
    assert all(c.token_count > 0 for c in chunks)


def test_table_aware_keeps_table_intact(table_section):
    ctx = ChunkContext("doc-1", target_tokens=512, overlap=0)
    chunks = TableAwareStrategy(512, 0).chunk(table_section, "doc-1", ctx)
    table_chunks = [c for c in chunks if "table" in c.block_types]
    assert table_chunks
    # Whole table present in one chunk
    assert any("Vacation | 12 | 5" in c.content for c in table_chunks)


def test_token_counting_matches_tiktoken(prose_section):
    ctx = ChunkContext("doc-1")
    chunks = RecursiveCharacterStrategy(512, 64).chunk(prose_section, "doc-1", ctx)
    for chunk in chunks:
        assert chunk.token_count == count_tokens(chunk.content)


def test_selector_rejects_empty_candidates(prose_section):
    selector = ChunkingSelector()
    chunks, result = selector.select(prose_section, document_id="doc-1")
    assert chunks
    assert result.chosen_strategy in STRATEGY_REGISTRY
    assert result.evaluation is not None
    assert result.evaluation.empty_chunks == 0


def test_selector_prefers_table_aware_for_tables(table_section):
    selector = ChunkingSelector()
    chunks, result = selector.select(table_section, document_id="doc-1")
    assert result.chosen_strategy == "table_aware"


def test_composite_chunk_covers_all_block_types(table_section):
    chunks = composite_chunk(table_section, document_id="doc-1")
    block_types = {bt for c in chunks for bt in c.block_types}
    assert "table" in block_types
    assert "text" in block_types or "paragraph" in block_types


def test_structural_evaluation_flags_coverage(prose_section):
    selector = ChunkingSelector()
    _, result = selector.select(prose_section, document_id="doc-1")
    evaluation = result.evaluation
    assert evaluation.structural.source_coverage > 0.5
    assert evaluation.structural.heading_preservation > 0.0


def test_subsections_are_never_dropped():
    """A non-recursive winning strategy must not discard child sections."""
    parent = DocumentSection(
        section_id="s1",
        title="Chương I",
        level=1,
        blocks=[
            DocumentBlock(
                block_id="b1",
                block_type=BlockType.HEADING,
                text="Chương I",
                metadata={"heading_level": 1},
            )
        ],
        subsections=[
            DocumentSection(
                section_id="s2",
                title="Điều 1",
                level=2,
                blocks=[
                    DocumentBlock(
                        block_id="b2",
                        block_type=BlockType.HEADING,
                        text="Điều 1",
                        metadata={"heading_level": 2},
                    ),
                    DocumentBlock(
                        block_id="b3",
                        block_type=BlockType.PARAGRAPH,
                        text="Nội dung điều 1 quy định về tội phạm và hình phạt.",
                    ),
                ],
            )
        ],
    )

    chunks, _ = ChunkingSelector().select(parent, document_id="doc-1")

    assert any("Nội dung điều 1" in chunk.content for chunk in chunks)
