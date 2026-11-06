"""Chunking strategies (Section 5.3 of the proposal).

Every strategy converts a DocumentSection (plus its blocks) into candidate
chunks. Strategies are pure functions of their input — no I/O — so the
candidate generator can evaluate several of them per section cheaply.
"""

from abc import ABC, abstractmethod

import tiktoken

from app.ingestion.chunking.types import Chunk
from app.ingestion.ir import BlockType, DocumentSection

_TOKENIZER = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_TOKENIZER.encode(text, disallowed_special=()))


class ChunkingStrategy(ABC):
    name: str = "base"
    version: str = "1.0"

    def __init__(self, target_tokens: int = 512, overlap_tokens: int = 64):
        self.target_tokens = target_tokens
        self.overlap_tokens = overlap_tokens

    @abstractmethod
    def chunk(
        self, section: DocumentSection, document_id: str, strategy: "ChunkContext"
    ) -> list[Chunk]:
        """Split a section into chunks."""


class ChunkContext:
    """Shared counters and configuration passed to strategies."""

    def __init__(self, document_id: str, target_tokens: int = 512, overlap: int = 64):
        self.document_id = document_id
        self.target_tokens = target_tokens
        self.overlap = overlap
        self._counter = 0

    def next_id(self, section_id: str) -> str:
        self._counter += 1
        return f"{self.document_id}-{section_id}-c{self._counter}"

    def make_chunk(
        self,
        section: DocumentSection,
        content: str,
        strategy_name: str,
        strategy_version: str,
        block_types: list[str] | None = None,
        parent_chunk_id: str | None = None,
        chunk_index: int = 0,
        metadata: dict | None = None,
    ) -> Chunk:
        return Chunk(
            chunk_id=self.next_id(section.section_id),
            document_id=self.document_id,
            section_id=section.section_id,
            content=content,
            token_count=count_tokens(content),
            page_start=section.page_start,
            page_end=section.page_end,
            heading_path=section.heading_path,
            block_types=block_types or [],
            strategy_name=strategy_name,
            strategy_version=strategy_version,
            parent_chunk_id=parent_chunk_id,
            chunk_index=chunk_index,
            metadata=metadata or {},
        )


# ---------------------------------------------------------------------------
# Strategy implementations
# ---------------------------------------------------------------------------


class FixedSizeStrategy(ChunkingStrategy):
    """Simple token-window splitting with overlap. Baseline strategy."""

    name = "fixed_size"

    def chunk(
        self, section: DocumentSection, document_id: str, ctx: ChunkContext
    ) -> list[Chunk]:
        text = section.text
        if not text:
            return []
        tokens = _TOKENIZER.encode(text, disallowed_special=())
        chunks: list[Chunk] = []
        step = max(ctx.target_tokens - ctx.overlap, 1)
        index = 0
        for start in range(0, len(tokens), step):
            window = tokens[start : start + ctx.target_tokens]
            if not window:
                break
            content = _TOKENIZER.decode(window).strip()
            if content:
                chunks.append(
                    ctx.make_chunk(
                        section,
                        content,
                        self.name,
                        self.version,
                        block_types=["text"],
                        chunk_index=index,
                    )
                )
                index += 1
            if start + ctx.target_tokens >= len(tokens):
                break
        return chunks


class RecursiveCharacterStrategy(ChunkingStrategy):
    """Recursive splitting on paragraph → line → sentence separators."""

    name = "recursive"
    SEPARATORS = ["\n\n", "\n", ". ", " ", ""]

    def chunk(
        self, section: DocumentSection, document_id: str, ctx: ChunkContext
    ) -> list[Chunk]:
        text = section.text
        if not text:
            return []
        pieces = self._split(text, 0, ctx.target_tokens)
        return self._merge_into_chunks(section, pieces, ctx)

    def _split(self, text: str, depth: int, target: int) -> list[str]:
        if count_tokens(text) <= target:
            return [text]
        if depth >= len(self.SEPARATORS):
            return [text]
        separator = self.SEPARATORS[depth]
        parts = text.split(separator) if separator else list(text)
        results: list[str] = []
        buffer = ""
        for part in parts:
            candidate = buffer + (separator if buffer else "") + part
            if count_tokens(candidate) <= target:
                buffer = candidate
            else:
                if buffer:
                    results.append(buffer)
                if count_tokens(part) > target:
                    results.extend(self._split(part, depth + 1, target))
                    buffer = ""
                else:
                    buffer = part
        if buffer:
            results.append(buffer)
        return results

    def _merge_into_chunks(
        self, section: DocumentSection, pieces: list[str], ctx: ChunkContext
    ) -> list[Chunk]:
        chunks: list[Chunk] = []
        buffer = ""
        index = 0
        for piece in pieces:
            candidate = (buffer + "\n\n" + piece).strip() if buffer else piece.strip()
            if count_tokens(candidate) <= ctx.target_tokens:
                buffer = candidate
            else:
                if buffer:
                    chunks.append(
                        ctx.make_chunk(
                            section,
                            buffer,
                            self.name,
                            self.version,
                            block_types=["text"],
                            chunk_index=index,
                        )
                    )
                    index += 1
                buffer = piece.strip()
        if buffer:
            chunks.append(
                ctx.make_chunk(
                    section,
                    buffer,
                    self.name,
                    self.version,
                    block_types=["text"],
                    chunk_index=index,
                )
            )
        return chunks


class StructureAwareStrategy(ChunkingStrategy):
    """Splits on section boundaries: heading → its content stays together."""

    name = "structure_aware"

    def chunk(
        self, section: DocumentSection, document_id: str, ctx: ChunkContext
    ) -> list[Chunk]:
        chunks: list[Chunk] = []
        index = 0

        # Emit each subsection as a unit when it fits; otherwise recurse
        units: list[tuple[str, list[str], str]] = []
        current_text: list[str] = []
        current_types: list[str] = []
        for block in section.blocks:
            current_text.append(block.text)
            current_types.append(block.block_type)
        if current_text:
            units.append(("\n\n".join(current_text), current_types, section.title))

        for text, block_types, title in units:
            if count_tokens(text) <= ctx.target_tokens:
                chunks.append(
                    ctx.make_chunk(
                        section,
                        text,
                        self.name,
                        self.version,
                        block_types=block_types,
                        chunk_index=index,
                        metadata={"subunit": title},
                    )
                )
                index += 1
            else:
                # Fall back to recursive splitting inside the oversized unit
                fallback = RecursiveCharacterStrategy(
                    ctx.target_tokens, ctx.overlap
                )
                sub_chunks = fallback.chunk(section, document_id, ctx)
                for sub in sub_chunks:
                    sub.strategy_name = self.name
                    sub.strategy_version = self.version
                    sub.chunk_index = index
                    chunks.append(sub)
                    index += 1

        for subsection in section.subsections:
            chunks.extend(self.chunk(subsection, document_id, ctx))
        return chunks


class ParagraphGroupingStrategy(ChunkingStrategy):
    """Groups whole paragraphs until the token target is reached."""

    name = "paragraph_grouping"

    def chunk(
        self, section: DocumentSection, document_id: str, ctx: ChunkContext
    ) -> list[Chunk]:
        chunks: list[Chunk] = []
        buffer: list[str] = []
        types: list[str] = []
        index = 0

        def flush() -> None:
            nonlocal index
            if buffer:
                chunks.append(
                    ctx.make_chunk(
                        section,
                        "\n\n".join(buffer),
                        self.name,
                        self.version,
                        block_types=list(types),
                        chunk_index=index,
                    )
                )
                index += 1
                buffer.clear()
                types.clear()

        for block in section.blocks:
            candidate = "\n\n".join(buffer + [block.text])
            if buffer and count_tokens(candidate) > ctx.target_tokens:
                flush()
            buffer.append(block.text)
            types.append(block.block_type)
            if count_tokens(block.text) > ctx.target_tokens and len(buffer) == 1:
                flush()  # single oversized paragraph — avoid unbounded chunks
        flush()
        return chunks


class TableAwareStrategy(ChunkingStrategy):
    """Keeps tables intact; splits surrounding prose separately."""

    name = "table_aware"

    def chunk(
        self, section: DocumentSection, document_id: str, ctx: ChunkContext
    ) -> list[Chunk]:
        chunks: list[Chunk] = []
        prose_blocks: list[str] = []
        prose_types: list[str] = []
        index = 0

        def flush_prose() -> None:
            nonlocal index
            if not prose_blocks:
                return
            text = "\n\n".join(prose_blocks)
            sub = RecursiveCharacterStrategy(ctx.target_tokens, ctx.overlap)
            for piece in sub._split(text, 0, ctx.target_tokens):
                if piece.strip():
                    chunks.append(
                        ctx.make_chunk(
                            section,
                            piece.strip(),
                            self.name,
                            self.version,
                            block_types=list(prose_types),
                            chunk_index=index,
                        )
                    )
                    index += 1
            prose_blocks.clear()
            prose_types.clear()

        for block in section.blocks:
            if block.block_type == BlockType.TABLE:
                flush_prose()
                chunks.append(
                    ctx.make_chunk(
                        section,
                        block.text,
                        self.name,
                        self.version,
                        block_types=["table"],
                        chunk_index=index,
                        metadata={"table": True},
                    )
                )
                index += 1
            else:
                prose_blocks.append(block.text)
                prose_types.append(block.block_type)
        flush_prose()

        for subsection in section.subsections:
            chunks.extend(self.chunk(subsection, document_id, ctx))
        return chunks


class SemanticChunkingStrategy(ChunkingStrategy):
    """Groups paragraphs while embedding similarity stays above a threshold.

    Uses cheap lexical similarity (token Jaccard) so candidate evaluation
    does not require an embedding call per section; the offline retrieval
    benchmark (Section 5.4) decides whether this strategy earns its cost.
    """

    name = "semantic"
    SIMILARITY_THRESHOLD = 0.25

    def chunk(
        self, section: DocumentSection, document_id: str, ctx: ChunkContext
    ) -> list[Chunk]:
        paragraphs = [b for b in section.blocks if b.text.strip()]
        if not paragraphs:
            return []

        groups: list[list[str]] = [[paragraphs[0].text]]
        for previous, current in zip(paragraphs, paragraphs[1:]):
            similarity = self._jaccard(previous.text, current.text)
            buffer_text = "\n\n".join(groups[-1] + [current.text])
            if (
                similarity >= self.SIMILARITY_THRESHOLD
                and count_tokens(buffer_text) <= ctx.target_tokens
            ):
                groups[-1].append(current.text)
            else:
                groups.append([current.text])

        chunks: list[Chunk] = []
        for index, group in enumerate(groups):
            content = "\n\n".join(group)
            if count_tokens(content) > ctx.target_tokens:
                sub = RecursiveCharacterStrategy(ctx.target_tokens, ctx.overlap)
                for piece in sub._split(content, 0, ctx.target_tokens):
                    if piece.strip():
                        chunks.append(
                            ctx.make_chunk(
                                section,
                                piece.strip(),
                                self.name,
                                self.version,
                                block_types=["text"],
                                chunk_index=len(chunks),
                            )
                        )
            else:
                chunks.append(
                    ctx.make_chunk(
                        section,
                        content,
                        self.name,
                        self.version,
                        block_types=["text"],
                        chunk_index=index,
                    )
                )
        return chunks

    @staticmethod
    def _jaccard(a: str, b: str) -> float:
        set_a = set(a.lower().split())
        set_b = set(b.lower().split())
        if not set_a or not set_b:
            return 0.0
        return len(set_a & set_b) / len(set_a | set_b)


class CodeAwareStrategy(ChunkingStrategy):
    """Never splits inside a fenced code block; keeps functions together."""

    name = "code_aware"

    def chunk(
        self, section: DocumentSection, document_id: str, ctx: ChunkContext
    ) -> list[Chunk]:
        chunks: list[Chunk] = []
        buffer: list[str] = []
        buffer_types: list[str] = []
        index = 0
        in_code = False

        def flush() -> None:
            nonlocal index
            if buffer:
                chunks.append(
                    ctx.make_chunk(
                        section,
                        "\n\n".join(buffer),
                        self.name,
                        self.version,
                        block_types=list(buffer_types),
                        chunk_index=index,
                    )
                )
                index += 1
                buffer.clear()
                buffer_types.clear()

        for block in section.blocks:
            is_code = block.block_type == BlockType.CODE or block.text.strip().startswith(
                "```"
            )
            if is_code and not in_code:
                flush()
                in_code = True
            buffer.append(block.text)
            buffer_types.append(block.block_type)
            if is_code and block.text.count("```") >= 2:
                flush()
                in_code = False
            elif not in_code and count_tokens("\n\n".join(buffer)) > ctx.target_tokens:
                flush()
        flush()
        return chunks


class SmallToBigStrategy(ChunkingStrategy):
    """Retrieval-oriented: small chunks for matching, parents for context.

    Each parent window produces one small child (first ~128 tokens) used as
    the retrieval unit; the parent id is stored so the retriever can expand
    to full context after matching (Section 11.3).
    """

    name = "small_to_big"
    CHILD_TOKENS = 128

    def chunk(
        self, section: DocumentSection, document_id: str, ctx: ChunkContext
    ) -> list[Chunk]:
        parents = RecursiveCharacterStrategy(ctx.target_tokens, ctx.overlap).chunk(
            section, document_id, ctx
        )
        chunks: list[Chunk] = []
        for parent in parents:
            parent.strategy_name = self.name
            parent.strategy_version = self.version
            tokens = _TOKENIZER.encode(parent.content, disallowed_special=())
            child_content = _TOKENIZER.decode(tokens[: self.CHILD_TOKENS]).strip()
            child = ctx.make_chunk(
                section,
                child_content,
                self.name,
                self.version,
                block_types=["text"],
                parent_chunk_id=parent.chunk_id,
                chunk_index=parent.chunk_index,
                metadata={"is_child": True},
            )
            chunks.append(child)
            # Parent indexed too, marked for context expansion only
            parent.metadata["is_parent"] = True
            chunks.append(parent)
        return chunks


class ContextualRetrievalStrategy(ChunkingStrategy):
    """Prepends section title + heading path as context to each chunk.

    Mirrors contextual retrieval: each chunk is prefixed with a short
    situating description derived from its position in the document. The
    LLM-generated variant (Section 5.3) is an offline enrichment step; this
    deterministic version works without model calls.
    """

    name = "contextual"

    def chunk(
        self, section: DocumentSection, document_id: str, ctx: ChunkContext
    ) -> list[Chunk]:
        base = RecursiveCharacterStrategy(ctx.target_tokens, ctx.overlap).chunk(
            section, document_id, ctx
        )
        context_prefix = " > ".join(section.heading_path) or section.title
        for chunk in base:
            chunk.strategy_name = self.name
            chunk.strategy_version = self.version
            if context_prefix:
                chunk.content = f"[Section: {context_prefix}]\n\n{chunk.content}"
                chunk.token_count = count_tokens(chunk.content)
        return base


STRATEGY_REGISTRY: dict[str, type[ChunkingStrategy]] = {
    cls.name: cls
    for cls in (
        FixedSizeStrategy,
        RecursiveCharacterStrategy,
        StructureAwareStrategy,
        ParagraphGroupingStrategy,
        TableAwareStrategy,
        SemanticChunkingStrategy,
        CodeAwareStrategy,
        SmallToBigStrategy,
        ContextualRetrievalStrategy,
    )
}
