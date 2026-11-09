"""Ingestion pipeline orchestration (Sections 4–5, 11).

    upload → parse → quality gate → section build → adaptive chunk
           → embed → index (versioned chunk set) → mark ready

Each stage records timing/cost on the run for observability. Failures set
DocumentStatus.FAILED with an error message surfaced by the API.
"""

import time
from uuid import uuid4

from app.domain.documents.entities import Document, DocumentStatus
from app.infrastructure.vectorstores.sparse import SparseEncoder
from app.ingestion.analyzer import DocumentAnalyzer
from app.ingestion.chunking.selection import ChunkingSelector
from app.ingestion.document_type import detect_document_type
from app.ingestion.embedding import EmbeddingPipeline
from app.ingestion.indexing import QdrantIndexer
from app.ingestion.legal_metadata import extract_document_metadata, normalize_text
from app.ingestion.parsers import PdfParser, get_parser
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

            # 1. Parse → Canonical Document IR. Document type selects the
            #    layout-aware parser for scientific/technical PDFs.
            document_type = detect_document_type(
                document.file_type, file_bytes, document.filename
            )
            document.run_metadata = {
                **(document.run_metadata or {}),
                "document_type": document_type,
            }
            parser = get_parser(document.file_type, document_type=document_type)
            parser_fallback = False
            try:
                ir = parser.parse(
                    file_bytes,
                    document_id=str(document.id),
                    filename=document.filename,
                )
            except Exception as exc:  # noqa: BLE001 - Docling is optional
                if parser.name != "docling":
                    raise
                import logging

                logging.getLogger(__name__).warning(
                    "Docling failed, falling back to PyMuPDF: %s", exc
                )
                parser = PdfParser()
                parser_fallback = True
                ir = parser.parse(
                    file_bytes,
                    document_id=str(document.id),
                    filename=document.filename,
                )

            if ir.metadata.get("document_type") == "scientific":
                title = ir.metadata.get("title") or document.filename.rsplit(".", 1)[0]
                document.title = title
                document.title_normalized = normalize_text(title)
                document.document_number = ir.metadata.get("arxiv_id")
                document.document_year = ir.metadata.get("document_year")
            else:
                # Legal identity is needed for exact Điều/Khoản/Điểm lookup.
                metadata = extract_document_metadata(document.filename, ir)
                document.title = metadata.title
                document.title_normalized = metadata.title_normalized
                document.document_number = metadata.document_number
                document.document_year = metadata.document_year

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

            # 5. Embed (dense) + encode the sparse channel from the same text
            embedded = await self.embedding_pipeline.embed_chunks(all_chunks, document)
            sparse_encoder = SparseEncoder()
            sparse_vectors = {
                chunk.chunk_id: sparse_encoder.encode(
                    EmbeddingPipeline._embed_text(chunk)
                )
                for chunk in all_chunks
            }

            # 6. Index under a fresh chunk-set version (Section 9.5)
            #    Publish-after-verify: the new set must be complete before
            #    the old one is retired — never the other way around.
            chunk_set_id = str(uuid4())
            self.indexer.ensure_collection()
            written = self.indexer.upsert_chunks(
                embedded, chunk_set_id=chunk_set_id, sparse_vectors=sparse_vectors
            )
            if written == 0:
                document.status = DocumentStatus.FAILED
                document.error_message = "Indexing wrote no points"
                return document

            # Retire the previous set only now that the new one is queryable
            previous_chunk_set = document.run_metadata.get("chunk_set_id")
            if previous_chunk_set and previous_chunk_set != chunk_set_id:
                self.indexer.deactivate_chunk_set(
                    previous_chunk_set, document.tenant_id
                )

            document.active_chunk_set_id = chunk_set_id

            document.status = (
                DocumentStatus.DEGRADED
                if quality_report.requires_manual_review
                else DocumentStatus.READY
            )
            document.run_metadata = {
                "chunk_set_id": chunk_set_id,
                "strategy_runs": strategy_runs,
                "processing_seconds": round(time.monotonic() - started, 2),
                "document_type": document_type,
                "parser_name": parser.name,
                "parser_version": parser.version,
                "parser_fallback": parser_fallback,
            }
            return document
        except Exception as exc:  # surface any pipeline failure to the API
            document.status = DocumentStatus.FAILED
            document.error_message = f"{type(exc).__name__}: {exc}"
            return document
