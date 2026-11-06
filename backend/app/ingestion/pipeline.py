"""Ingestion pipeline orchestration (Sections 4–5, 11).

    upload → parse → quality gate → section build → adaptive chunk
           → embed → index (versioned chunk set) → mark ready

Each stage records timing/cost on the run for observability. Failures set
DocumentStatus.FAILED with an error message surfaced by the API.
"""

import time
from uuid import uuid4

from app.domain.documents.entities import Document, DocumentStatus
from app.ingestion.analyzer import DocumentAnalyzer
from app.ingestion.chunking.selection import ChunkingSelector
from app.ingestion.embedding import EmbeddingPipeline
from app.ingestion.indexing import QdrantIndexer
from app.ingestion.parsers import get_parser
from app.ingestion.section_builder import SectionBuilder


class IngestionPipeline:
    """Runs the full ingestion flow for one document."""

    def __init__(
        self,
        embedding_pipeline: EmbeddingPipeline,
        indexer: QdrantIndexer,
        analyzer: DocumentAnalyzer | None = None,
        section_builder: SectionBuilder | None = None,
        selector: ChunkingSelector | None = None,
        target_tokens: int = 512,
        overlap_tokens: int = 64,
    ):
        self.embedding_pipeline = embedding_pipeline
        self.indexer = indexer
        self.analyzer = analyzer or DocumentAnalyzer()
        self.section_builder = section_builder or SectionBuilder()
        self.selector = selector or ChunkingSelector()
        self.target_tokens = target_tokens
        self.overlap_tokens = overlap_tokens

    async def run(self, document: Document, file_bytes: bytes) -> Document:
        started = time.monotonic()
        try:
            document.status = DocumentStatus.PROCESSING

            # 1. Parse → Canonical Document IR
            parser = get_parser(document.file_type)
            ir = parser.parse(file_bytes, document_id=str(document.id),
                              filename=document.filename)

            # 2. Quality gate (Section 4.4) — runs before chunking
            quality_report = self.analyzer.analyze(ir)
            document.quality_report = quality_report

            if quality_report.requires_ocr:
                # Do not enter adaptive chunking on unparseable input
                document.status = DocumentStatus.FAILED
                document.error_message = (
                    "Document requires OCR before it can be indexed"
                )
                return document

            # 3. Sections
            ir = self.section_builder.build(ir)

            # 4. Adaptive chunking per section (constraint-first selection)
            all_chunks = []
            strategy_runs: list[dict] = []
            for section in ir.sections:
                chunks, result = self.selector.select(
                    section,
                    document_id=str(document.id),
                    target_tokens=self.target_tokens,
                    overlap_tokens=self.overlap_tokens,
                )
                all_chunks.extend(chunks)
                strategy_runs.append(
                    {
                        "section_id": section.section_id,
                        "strategy": result.chosen_strategy,
                        "composite": result.composite,
                        "chunk_count": len(chunks),
                    }
                )

            if not all_chunks:
                document.status = DocumentStatus.FAILED
                document.error_message = "No content extracted from document"
                return document

            # 5. Embed
            embedded = await self.embedding_pipeline.embed_chunks(all_chunks, document)

            # 6. Index under a fresh chunk-set version (Section 9.5)
            chunk_set_id = str(uuid4())
            self.indexer.ensure_collection()
            self.indexer.upsert_chunks(embedded, chunk_set_id=chunk_set_id)

            document.status = (
                DocumentStatus.DEGRADED
                if quality_report.requires_manual_review
                else DocumentStatus.READY
            )
            document.run_metadata = {
                "chunk_set_id": chunk_set_id,
                "strategy_runs": strategy_runs,
                "processing_seconds": round(time.monotonic() - started, 2),
            }
            return document
        except Exception as exc:  # surface any pipeline failure to the API
            document.status = DocumentStatus.FAILED
            document.error_message = f"{type(exc).__name__}: {exc}"
            return document
