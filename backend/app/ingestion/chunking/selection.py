"""Adaptive chunking selection (Section 5.3/5.4).

Constraint-first policy:
  1. Generate candidate strategy set per section (rule-based, section type).
  2. Evaluate each candidate (structural + semantic, plus offline retrieval
     metrics when the section appears in the evaluation benchmark).
  3. Eliminate hard-constraint violations (token limits, empty chunks).
  4. Rank survivors; optionally run the LLM judge as a tie-breaker only.
  5. A strategy may be composite: block-type → splitter mapping inside one
     section (prose → recursive, table → table-aware, code → code-aware).
"""

from pydantic import BaseModel, Field

from app.ingestion.chunking.evaluation import CandidateEvaluation, ChunkingEvaluator
from app.ingestion.chunking.strategies import (
    STRATEGY_REGISTRY,
    ChunkContext,
    ChunkingStrategy,
    CodeAwareStrategy,
    RecursiveCharacterStrategy,
    TableAwareStrategy,
)
from app.ingestion.chunking.types import Chunk
from app.ingestion.ir import BlockType, DocumentSection


class StrategyCandidate(BaseModel):
    strategy_name: str
    reason: str = ""


class SelectionResult(BaseModel):
    chosen_strategy: str
    composite: bool = False
    evaluation: CandidateEvaluation | None = None
    rejected: list[dict] = Field(default_factory=list)


class ChunkingSelector:
    """Rule-based candidate generation + constraint-first selection."""

    PROSE_ONLY = {BlockType.PARAGRAPH, BlockType.HEADING, BlockType.LIST_ITEM}
    # Only these strategies walk `section.subsections` themselves.
    RECURSIVE_STRATEGIES = {"structure_aware", "table_aware"}

    def __init__(self, evaluator: ChunkingEvaluator | None = None):
        self.evaluator = evaluator or ChunkingEvaluator()

    # ------------------------------------------------------------------
    # Candidate generation (deterministic; no LLM)
    # ------------------------------------------------------------------

    def generate_candidates(self, section: DocumentSection) -> list[StrategyCandidate]:
        block_types = {b.block_type for b in section.blocks}
        candidates: list[StrategyCandidate] = []

        if BlockType.TABLE in block_types:
            candidates.append(
                StrategyCandidate(strategy_name="table_aware", reason="section contains tables")
            )
        if BlockType.CODE in block_types:
            candidates.append(
                StrategyCandidate(strategy_name="code_aware", reason="section contains code blocks")
            )

        candidates.append(
            StrategyCandidate(strategy_name="recursive", reason="default prose splitter")
        )

        if section.subsections:
            candidates.append(
                StrategyCandidate(strategy_name="structure_aware", reason="section has subsections")
            )

        block_count = len(section.blocks)
        if block_count >= 6:
            candidates.append(
                StrategyCandidate(strategy_name="paragraph_grouping", reason="many paragraphs")
            )
        if block_count >= 10:
            candidates.append(
                StrategyCandidate(strategy_name="semantic", reason="long section benefits from grouping")
            )

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[StrategyCandidate] = []
        for candidate in candidates:
            if candidate.strategy_name not in seen:
                seen.add(candidate.strategy_name)
                unique.append(candidate)
        return unique

    # ------------------------------------------------------------------
    # Evaluation + selection
    # ------------------------------------------------------------------

    def select(
        self,
        section: DocumentSection,
        document_id: str,
        target_tokens: int = 512,
        overlap_tokens: int = 64,
        retrieval_metrics_by_strategy: dict[str, dict[str, float]] | None = None,
    ) -> tuple[list[Chunk], SelectionResult]:
        """Evaluate candidates for a section and return the winning chunks."""
        candidates = self.generate_candidates(section)
        retrieval_metrics_by_strategy = retrieval_metrics_by_strategy or {}

        evaluated: list[tuple[StrategyCandidate, list[Chunk], CandidateEvaluation]] = []
        rejected: list[dict] = []

        for candidate in candidates:
            strategy_cls = STRATEGY_REGISTRY.get(candidate.strategy_name)
            if strategy_cls is None:
                continue
            strategy: ChunkingStrategy = strategy_cls(target_tokens, overlap_tokens)
            ctx = ChunkContext(document_id, target_tokens, overlap_tokens)
            chunks = strategy.chunk(section, document_id, ctx)
            if not chunks:
                rejected.append(
                    {"strategy": candidate.strategy_name, "reason": "no chunks produced"}
                )
                continue

            evaluation = self.evaluator.evaluate(
                chunks,
                section,
                strategy_name=candidate.strategy_name,
                strategy_version=strategy.version,
                target_tokens=target_tokens,
                retrieval_metrics=retrieval_metrics_by_strategy.get(
                    candidate.strategy_name
                ),
            )

            # Hard constraints
            if evaluation.empty_chunks > 0:
                rejected.append(
                    {
                        "strategy": candidate.strategy_name,
                        "reason": f"{evaluation.empty_chunks} empty chunks",
                    }
                )
                continue
            if evaluation.exceeds_max_tokens:
                rejected.append(
                    {
                        "strategy": candidate.strategy_name,
                        "reason": "token limit exceeded",
                    }
                )
                continue

            evaluated.append((candidate, chunks, evaluation))

        if not evaluated:
            # Fallback: recursive always runs; relax constraints rather than fail
            strategy = RecursiveCharacterStrategy(target_tokens, overlap_tokens)
            ctx = ChunkContext(document_id, target_tokens, overlap_tokens)
            chunks = strategy.chunk(section, document_id, ctx)
            chunks = self._include_subsections(
                section,
                chunks,
                "recursive",
                document_id,
                target_tokens,
                overlap_tokens,
                retrieval_metrics_by_strategy,
            )
            evaluation = self.evaluator.evaluate(
                chunks, section, "recursive", strategy.version, target_tokens
            )
            return chunks, SelectionResult(
                chosen_strategy="recursive",
                evaluation=evaluation,
                rejected=rejected,
            )

        # Composite short-circuit: table/code sections always win with their
        # specialized strategy when it passed constraints
        specialized = [
            item
            for item in evaluated
            if item[0].strategy_name in ("table_aware", "code_aware")
        ]
        if specialized:
            # Prefer the one matching the section's dominant block type
            block_types = {b.block_type for b in section.blocks}
            preferred = "table_aware" if BlockType.TABLE in block_types else "code_aware"
            for candidate, chunks, evaluation in specialized:
                if candidate.strategy_name == preferred:
                    chunks = self._include_subsections(
                        section,
                        chunks,
                        candidate.strategy_name,
                        document_id,
                        target_tokens,
                        overlap_tokens,
                        retrieval_metrics_by_strategy,
                    )
                    return chunks, SelectionResult(
                        chosen_strategy=candidate.strategy_name,
                        composite=True,
                        evaluation=evaluation,
                        rejected=rejected,
                    )

        # Rank: offline retrieval metrics (Layer 3) first when present,
        # then structural/semantic composites
        def rank(item: tuple[StrategyCandidate, list[Chunk], CandidateEvaluation]):
            _, _, evaluation = item
            retrieval = (
                evaluation.retrieval_recall_at_5 or 0.0,
                evaluation.retrieval_mrr or 0.0,
            )
            quality = 0.6 * evaluation.structural.composite + 0.4 * evaluation.semantic.composite
            return (*retrieval, quality)

        best_candidate, best_chunks, best_evaluation = max(evaluated, key=rank)
        best_chunks = self._include_subsections(
            section,
            best_chunks,
            best_candidate.strategy_name,
            document_id,
            target_tokens,
            overlap_tokens,
            retrieval_metrics_by_strategy,
        )
        return best_chunks, SelectionResult(
            chosen_strategy=best_candidate.strategy_name,
            composite=False,
            evaluation=best_evaluation,
            rejected=rejected,
        )

    def _include_subsections(
        self,
        section: DocumentSection,
        chunks: list[Chunk],
        strategy_name: str,
        document_id: str,
        target_tokens: int,
        overlap_tokens: int,
        retrieval_metrics_by_strategy: dict[str, dict[str, float]],
    ) -> list[Chunk]:
        """Never drop subsection content.

        `recursive`, `paragraph_grouping` and `semantic` only read
        `section.blocks`, so a winning strategy that does not recurse would
        silently discard every child section. Chunk those children with their
        own selection and append the result.
        """
        if not section.subsections or strategy_name in self.RECURSIVE_STRATEGIES:
            return chunks
        for subsection in section.subsections:
            sub_chunks, _ = self.select(
                subsection,
                document_id,
                target_tokens,
                overlap_tokens,
                retrieval_metrics_by_strategy=retrieval_metrics_by_strategy,
            )
            chunks.extend(sub_chunks)
        return chunks


# Composite helper: apply a block-type → splitter mapping inside a section.
def composite_chunk(
    section: DocumentSection,
    document_id: str,
    target_tokens: int = 512,
    overlap_tokens: int = 64,
) -> list[Chunk]:
    """Split prose with recursive, tables with table-aware, code with code-aware."""
    ctx = ChunkContext(document_id, target_tokens, overlap_tokens)
    chunks: list[Chunk] = []

    prose = [
        b
        for b in section.blocks
        if b.block_type in ChunkingSelector.PROSE_ONLY and b.text.strip()
    ]
    if prose:
        prose_section = section.model_copy(
            update={
                "blocks": prose,
                "subsections": [],
            }
        )
        chunks.extend(
            RecursiveCharacterStrategy(target_tokens, overlap_tokens).chunk(
                prose_section, document_id, ctx
            )
        )

    tables = [b for b in section.blocks if b.block_type == BlockType.TABLE]
    if tables:
        table_section = section.model_copy(update={"blocks": tables, "subsections": []})
        chunks.extend(
            TableAwareStrategy(target_tokens, overlap_tokens).chunk(
                table_section, document_id, ctx
            )
        )

    code = [b for b in section.blocks if b.block_type == BlockType.CODE]
    if code:
        code_section = section.model_copy(update={"blocks": code, "subsections": []})
        chunks.extend(
            CodeAwareStrategy(target_tokens, overlap_tokens).chunk(
                code_section, document_id, ctx
            )
        )

    return chunks
