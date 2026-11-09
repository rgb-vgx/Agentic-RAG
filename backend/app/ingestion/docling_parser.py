"""Docling-backed parser for scientific / layout-heavy PDFs.

Docling is an optional dependency: the base backend image stays small and
falls back to PyMuPDF when it is not installed. See
`docs/plans/2026-11-09-pdf-layout-benchmark.md` for the benchmark behind
this choice.
"""

from __future__ import annotations

import io
import os
import re
import threading

from app.ingestion.ir import BlockType, BoundingBox, CanonicalDocument, DocumentBlock
from app.ingestion.parsers import BaseParser

_SKIP_LABELS = {
    "page_header",
    "page_footer",
    "picture",
    "key_value_area",
}
_CONVERTER = None
_CONVERTER_LOCK = threading.Lock()


class DoclingUnavailableError(RuntimeError):
    """Raised when the optional Docling dependency is not installed."""


class DoclingParser(BaseParser):
    name = "docling"
    version = "2.x"
    supported_types = ("pdf",)

    def parse(self, data: bytes, document_id: str, filename: str) -> CanonicalDocument:
        try:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.document import DocumentStream
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption
        except ImportError as exc:  # pragma: no cover - depends on install
            raise DoclingUnavailableError(
                "Docling is not installed; install the optional extra "
                "`pip install .[docling]` or use the PyMuPDF parser"
            ) from exc

        converter = _get_converter(
            DocumentConverter,
            PdfFormatOption,
            PdfPipelineOptions,
            InputFormat,
        )
        stream = DocumentStream(name=filename, stream=io.BytesIO(data))
        result = converter.convert(stream)
        doc = result.document

        blocks: list[DocumentBlock] = []
        heading_stack: dict[int, str] = {}
        first_title: str | None = None
        block_index = 0

        for item, level in doc.iterate_items():
            label = _label(item)
            if label in _SKIP_LABELS:
                continue
            text = getattr(item, "text", None) or ""
            if label == "table":
                text = _table_markdown(item, doc)
            text = (text or "").strip()
            if not text:
                continue

            is_caption_heading = bool(
                re.match(r"^\s*(?:Figure|Table)\s+\d+", text, re.IGNORECASE)
            )
            if label == "section_header" and not is_caption_heading:
                heading_stack[level] = text
                for deeper in [key for key in heading_stack if key > level]:
                    heading_stack.pop(deeper, None)
                if first_title is None:
                    first_title = text
                block_type = BlockType.HEADING
            elif label == "formula":
                block_type = BlockType.PARAGRAPH
            elif label == "code":
                block_type = BlockType.CODE
            elif label == "table":
                block_type = BlockType.TABLE
            else:
                block_type = BlockType.PARAGRAPH

            prov = _first_prov(item)
            page = prov.get("page_no") if prov else None
            bbox = prov.get("bbox") if prov else None
            blocks.append(
                DocumentBlock(
                    block_id=f"{document_id}-docling-b{block_index}",
                    block_type=block_type,
                    text=text,
                    bbox=_bbox(page, bbox),
                    page_start=page,
                    page_end=page,
                    block_index=block_index,
                    heading_path=[
                        heading_stack[key] for key in sorted(heading_stack)
                    ],
                    table_data=(
                        _markdown_table_rows(text) if label == "table" else None
                    ),
                    metadata={
                        "docling_label": label,
                        "content_type": (
                            "formula" if label == "formula"
                            else "table" if label == "table"
                            else "text"
                        ),
                    },
                )
            )
            block_index += 1

        metadata = {
            "document_type": "scientific",
            "title": first_title,
        }
        arxiv = re.search(r"arXiv:\s*([0-9.]+v\d+)", "\n".join(
            block.text for block in blocks[:80]
        ))
        if arxiv:
            metadata["arxiv_id"] = arxiv.group(1)
        year = re.search(r"\b(19|20)\d{2}\b", " ".join(
            block.text for block in blocks[:20]
        ))
        if year:
            metadata["document_year"] = int(year.group(0))

        return CanonicalDocument(
            document_id=document_id,
            filename=filename,
            file_type="pdf",
            blocks=blocks,
            total_pages=len(doc.pages),
            parser_name=self.name,
            parser_version=self.version,
            metadata=metadata,
        )


def _get_converter(
    document_converter_cls,
    pdf_format_option_cls,
    pdf_pipeline_options_cls,
    input_format,
):
    global _CONVERTER
    if _CONVERTER is not None:
        return _CONVERTER
    with _CONVERTER_LOCK:
        if _CONVERTER is not None:
            return _CONVERTER
        options = pdf_pipeline_options_cls()
        options.do_table_structure = True
        options.do_formula_enrichment = True
        options.do_ocr = os.getenv("DOCLING_OCR", "false").lower() == "true"
        options.generate_page_images = False
        options.generate_picture_images = False
        options.accelerator_options.device = os.getenv("DOCLING_DEVICE", "auto")
        options.accelerator_options.num_threads = int(
            os.getenv("DOCLING_NUM_THREADS", "4")
        )
        _CONVERTER = document_converter_cls(
            format_options={
                input_format.PDF: pdf_format_option_cls(pipeline_options=options)
            }
        )
    return _CONVERTER


def _label(item) -> str:
    label = getattr(item, "label", "")
    return getattr(label, "value", label) or ""


def _first_prov(item) -> dict:
    prov = getattr(item, "prov", None) or []
    if not prov:
        return {}
    first = prov[0]
    return {
        "page_no": getattr(first, "page_no", None),
        "bbox": getattr(first, "bbox", None),
    }


def _bbox(page, bbox) -> BoundingBox | None:
    if bbox is None:
        return None
    return BoundingBox(
        page=page or 0,
        x0=float(getattr(bbox, "l", 0) or 0),
        y0=float(getattr(bbox, "t", 0) or 0),
        x1=float(getattr(bbox, "r", 0) or 0),
        y1=float(getattr(bbox, "b", 0) or 0),
    )


def _markdown_table_rows(markdown: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells if cell):
            continue
        rows.append(cells)
    return rows


def _table_markdown(item, doc) -> str:
    try:
        return item.export_to_markdown(doc=doc)
    except Exception:  # noqa: BLE001 - table export is best-effort
        return ""
