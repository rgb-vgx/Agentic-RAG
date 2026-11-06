"""Chunk candidate evaluation: structural + semantic scoring (Section 5.4).

Layer 1 (structural) and Layer 2 (semantic) are computed per candidate set.
Layer 3 (retrieval metrics) runs offline against the evaluation dataset and
lives in app/evaluation — its results calibrate the selection policy here.
"""

from pydantic import BaseModel, Field

from app.ingestion.chunking.types import Chunk
from app.ingestion.ir import DocumentSection


class StructuralScore(BaseModel):
    """Layer 1 — measurable from chunk text and provenance alone."""

    heading_preservation: float = Field(ge=0.0, le=1.0, default=1.0)
    paragraph_integrity: float = Field(ge=0.0, le=1.0, default=1.0)
    table_integrity: float = Field(ge=0.0, le=1.0, default=1.0)
    source_coverage: float = Field(ge=0.0, le=1.0, default=1.0)
    duplicate_penalty: float = Field(ge=0.0, le=1.0, default=0.0)

    @property
    def composite(self) -> float:
        raw = (
            0.25 * self.heading_preservation
            + 0.25 * self.paragraph_integrity
            + 0.20 * self.table_integrity
            + 0.20 * self.source_coverage
            + 0.10 * (1.0 - self.duplicate_penalty)
        )
        return round(raw, 4)


class SemanticScore(BaseModel):
    """Layer 2 — lexical proxies for cohesion (no LLM call required)."""

    semantic_cohesion: float = Field(ge=0.0, le=1.0, default=0.0)
    context_completeness: float = Field(ge=0.0, le=1.0, default=0.0)
    topic_consistency: float = Field(ge=0.0, le=1.0, default=0.0)
    reference_completeness: float = Field(ge=0.0, le=1.0, default=0.0)

    @property
    def composite(self) -> float:
        raw = (
            0.35 * self.semantic_cohesion
            + 0.30 * self.context_completeness
            + 0.20 * self.topic_consistency
            + 0.15 * self.reference_completeness
        )
        return round(raw, 4)


class CandidateEvaluation(BaseModel):
    strategy_name: str
    strategy_version: str = "1.0"

    structural: StructuralScore = Field(default_factory=StructuralScore)
    semantic: SemanticScore = Field(default_factory=SemanticScore)

    # Hard constraint results (Section 5.4 constraint-first policy)
    token_compliant: bool = True
    exceeds_max_tokens: bool = False
    empty_chunks: int = 0

    # Offline retrieval benchmark lookup (Layer 3), when available
    retrieval_recall_at_5: float | None = None
    retrieval_mrr: float | None = None

    total_tokens: int = 0
    chunk_count: int = 0


class ChunkingEvaluator:
    """Scores a candidate chunk set against structural and semantic criteria."""

    def evaluate(
        self,
        chunks: list[Chunk],
        section: DocumentSection,
        strategy_name: str,
        strategy_version: str = "1.0",
        target_tokens: int = 512,
        retrieval_metrics: dict[str, float] | None = None,
    ) -> CandidateEvaluation:
        structural = self._structural(chunks, section)
        semantic = self._semantic(chunks, section)

        empty = sum(1 for c in chunks if not c.content.strip())
        exceeds = any(c.token_count > int(target_tokens * 1.25) for c in chunks)

        evaluation = CandidateEvaluation(
            strategy_name=strategy_name,
            strategy_version=strategy_version,
            structural=structural,
            semantic=semantic,
            token_compliant=not exceeds,
            exceeds_max_tokens=exceeds,
            empty_chunks=empty,
            total_tokens=sum(c.token_count for c in chunks),
            chunk_count=len(chunks),
        )
        if retrieval_metrics:
            evaluation.retrieval_recall_at_5 = retrieval_metrics.get("recall_at_5")
            evaluation.retrieval_mrr = retrieval_metrics.get("mrr")
        return evaluation

    # ------------------------------------------------------------------

    def _structural(
        self, chunks: list[Chunk], section: DocumentSection
    ) -> StructuralScore:
        if not chunks:
            return StructuralScore(
                heading_preservation=0.0,
                paragraph_integrity=0.0,
                source_coverage=0.0,
            )

        # Heading preservation: every chunk carries the section heading path
        with_heading = sum(1 for c in chunks if c.heading_path)
        heading_preservation = with_heading / len(chunks)

        # Paragraph integrity: no chunk starts mid-sentence (heuristic —
        # a chunk beginning with a lowercase letter or conjunction is suspect)
        suspect = 0
        for chunk in chunks:
            first = chunk.content.strip()
            if first and first[0].islower():
                suspect += 1
        paragraph_integrity = 1.0 - suspect / len(chunks)

        # Table integrity: table chunks contain all rows of their source table
        table_integrity = 1.0
        table_chunks = [c for c in chunks if "table" in c.block_types]
        source_tables = [
            b for b in section.blocks if b.table_data is not None
        ]
        if source_tables:
            covered = sum(
                1
                for table in source_tables
                if any(table.text.split("\n")[0] in c.content for c in table_chunks)
            )
            table_integrity = covered / len(source_tables)

        # Source coverage: section text (normalized) fully represented
        section_text = section.text.lower().split()
        if section_text:
            chunk_words = set(" ".join(c.content for c in chunks).lower().split())
            section_words = set(section_text)
            source_coverage = len(chunk_words & section_words) / len(section_words)
        else:
            source_coverage = 1.0

        # Duplicate content between chunks
        duplicate_penalty = self._duplicate_penalty(chunks)

        return StructuralScore(
            heading_preservation=round(heading_preservation, 3),
            paragraph_integrity=round(paragraph_integrity, 3),
            table_integrity=round(table_integrity, 3),
            source_coverage=round(min(source_coverage, 1.0), 3),
            duplicate_penalty=round(duplicate_penalty, 3),
        )

    @staticmethod
    def _duplicate_penalty(chunks: list[Chunk]) -> float:
        if len(chunks) < 2:
            return 0.0
        seen: dict[str, int] = {}
        for chunk in chunks:
            key = " ".join(chunk.content.lower().split()[:30])
            seen[key] = seen.get(key, 0) + 1
        duplicated = sum(count - 1 for count in seen.values() if count > 1)
        return min(duplicated / len(chunks), 1.0)

    def _semantic(self, chunks: list[Chunk], section: DocumentSection) -> SemanticScore:
        if not chunks:
            return SemanticScore()

        # Cohesion: average pairwise token overlap inside each chunk
        cohesions = []
        for chunk in chunks:
            words = chunk.content.lower().split()
            if len(words) < 5:
                cohesions.append(0.5)
                continue
            head = set(words[: len(words) // 2])
            tail = set(words[len(words) // 2 :])
            union = head | tail
            cohesions.append(len(head & tail) / len(union) if union else 0.0)
        cohesion = sum(cohesions) / len(cohesions)

        # Context completeness: share of chunks with heading context
        with_context = sum(
            1 for c in chunks if c.heading_path or "[Section:" in c.content[:60]
        )
        completeness = with_context / len(chunks)

        # Topic consistency: vocabulary overlap across chunks of the section
        vocabularies = [set(c.content.lower().split()) for c in chunks]
        union: set[str] = set()
        for vocab in vocabularies:
            union |= vocab
        if union:
            consistent = sum(
                1 for vocab in vocabularies if len(vocab & union) / len(union) > 0.15
            )
            consistency = consistent / len(vocabularies)
        else:
            consistency = 0.0

        # Reference completeness: no chunk ends with a dangling reference
        dangling = sum(
            1
            for c in chunks
            if c.content.rstrip().endswith(("see", "as mentioned", "refer to", ":"))
        )
        reference = 1.0 - dangling / len(chunks)

        return SemanticScore(
            semantic_cohesion=round(cohesion, 3),
            context_completeness=round(completeness, 3),
            topic_consistency=round(consistency, 3),
            reference_completeness=round(reference, 3),
        )
