"""Document parsers: file bytes → Canonical Document IR (Section 4.1)."""

from abc import ABC, abstractmethod

from app.ingestion.ir import BlockType, BoundingBox, CanonicalDocument, DocumentBlock


class BaseParser(ABC):
    """Interface every format-specific parser implements."""

    name: str = "base"
    version: str = "1.0"
    supported_types: tuple[str, ...] = ()

    @abstractmethod
    def parse(self, data: bytes, document_id: str, filename: str) -> CanonicalDocument:
        """Convert raw file bytes into Canonical Document IR."""


class TxtParser(BaseParser):
    name = "txt"
    version = "1.0"
    supported_types = ("txt", "text", "md", "markdown")

    def parse(self, data: bytes, document_id: str, filename: str) -> CanonicalDocument:
        text = data.decode("utf-8", errors="replace")
        blocks: list[DocumentBlock] = []
        for index, chunk in enumerate(text.split("\n\n")):
            stripped = chunk.strip()
            if not stripped:
                continue
            is_heading = stripped.startswith("#")
            blocks.append(
                DocumentBlock(
                    block_id=f"{document_id}-b{index}",
                    block_type=BlockType.HEADING if is_heading else BlockType.PARAGRAPH,
                    text=stripped.lstrip("# ").strip() if is_heading else stripped,
                    page_start=1,
                    page_end=1,
                    block_index=index,
                    metadata={"heading_level": stripped.count("#")} if is_heading else {},
                )
            )
        return CanonicalDocument(
            document_id=document_id,
            filename=filename,
            file_type="txt",
            blocks=blocks,
            total_pages=1,
            parser_name=self.name,
            parser_version=self.version,
        )


class PdfParser(BaseParser):
    """PyMuPDF-based PDF parser (Section 4.1).

    Extracts text spans with position, detects headings by font size and
    records page numbers so every block carries provenance. Reading-order
    and layout confidence are reported by the quality analyzer, not here.
    """

    name = "pymupdf"
    version = "1.0"
    supported_types = ("pdf",)

    def parse(self, data: bytes, document_id: str, filename: str) -> CanonicalDocument:
        import fitz  # PyMuPDF

        blocks: list[DocumentBlock] = []
        with fitz.open(stream=data, filetype="pdf") as document:
            page_count = document.page_count
            # Heuristic: collect font sizes across pages to derive heading threshold
            font_sizes: list[float] = []
            for page in document:
                for span in page.get_text("dict").get("spans", []):
                    if span.get("text", "").strip():
                        font_sizes.append(span.get("size", 0.0))
            heading_threshold = self._heading_threshold(font_sizes)

            for page_number, page in enumerate(document, start=1):
                block_index = 0
                for block in page.get_text("dict").get("blocks", []):
                    if block.get("type", 1) != 0:  # 1 = image block
                        continue
                    for line in block.get("lines", []):
                        spans = line.get("spans", [])
                        if not spans:
                            continue
                        text = "".join(span.get("text", "") for span in spans).strip()
                        if not text:
                            continue
                        max_size = max(span.get("size", 0.0) for span in spans)
                        is_bold = any(
                            "bold" in span.get("font", "").lower() for span in spans
                        )
                        is_heading = max_size >= heading_threshold or (
                            is_bold and len(text) < 120 and not text.endswith(".")
                        )
                        bbox = block.get("bbox", [0, 0, 0, 0])
                        blocks.append(
                            DocumentBlock(
                                block_id=f"{document_id}-p{page_number}-b{block_index}",
                                block_type=BlockType.HEADING if is_heading else BlockType.PARAGRAPH,
                                text=text,
                                bbox=BoundingBox(
                                    page=page_number,
                                    x0=bbox[0],
                                    y0=bbox[1],
                                    x1=bbox[2],
                                    y1=bbox[3],
                                ),
                                page_start=page_number,
                                page_end=page_number,
                                block_index=len(blocks),
                                metadata={
                                    "font_size": round(max_size, 1),
                                    "is_bold": is_bold,
                                },
                            )
                        )
                        block_index += 1

                # Extract tables as structured data, preserving position
                for table_number, table in enumerate(self._extract_tables(page)):
                    blocks.append(
                        DocumentBlock(
                            block_id=f"{document_id}-p{page_number}-t{table_number}",
                            block_type=BlockType.TABLE,
                            text="\n".join(" | ".join(row) for row in table),
                            page_start=page_number,
                            page_end=page_number,
                            block_index=len(blocks),
                            table_data=table,
                        )
                    )

        return CanonicalDocument(
            document_id=document_id,
            filename=filename,
            file_type="pdf",
            blocks=blocks,
            total_pages=page_count,
            parser_name=self.name,
            parser_version=self.version,
        )

    @staticmethod
    def _heading_threshold(font_sizes: list[float]) -> float:
        """Body text size = most common font size; headings sit above it."""
        if not font_sizes:
            return 12.0
        from collections import Counter

        rounded = Counter(round(size, 1) for size in font_sizes)
        body_size = rounded.most_common(1)[0][0]
        return body_size + 1.5

    @staticmethod
    def _extract_tables(page) -> list[list[list[str]]]:
        try:
            return [
                [[cell.strip() for cell in row] for row in table.extract()]
                for table in page.find_tables()
            ]
        except Exception:  # table detection is best-effort
            return []


PARSER_REGISTRY: dict[str, BaseParser] = {}


def get_parser(file_type: str) -> BaseParser:
    normalized = file_type.lower().lstrip(".")
    for parser in (PdfParser(), TxtParser()):
        if normalized in parser.supported_types:
            return parser
    raise ValueError(f"No parser registered for file type: {file_type}")
