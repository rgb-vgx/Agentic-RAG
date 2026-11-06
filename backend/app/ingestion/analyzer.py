"""Document analyzer and quality assessment (Section 4.4).

Runs immediately after parsing, before the Section Builder. Detects
extraction problems (repeating headers/footers, crossed reading order,
image-only pages) and produces the DocumentQualityReport gate.
"""

from statistics import median

from app.domain.documents.entities import DocumentQualityReport
from app.ingestion.ir import BlockType, CanonicalDocument, DocumentBlock


class DocumentAnalyzer:
    """Deterministic quality analysis of a Canonical Document."""

    # Fraction of pages whose first/last lines repeat → header/footer noise
    HEADER_FOOTER_THRESHOLD = 0.6
    # Minimum characters per page for a text-based PDF
    MIN_CHARS_PER_PAGE = 50
    # Confidence below which manual review is flagged
    MANUAL_REVIEW_THRESHOLD = 0.55

    def analyze(self, document: CanonicalDocument) -> DocumentQualityReport:
        warnings: list[str] = []

        text_blocks = [b for b in document.blocks if b.text.strip()]
        total_chars = sum(len(b.text) for b in text_blocks)

        requires_ocr = self._is_scanned(document, total_chars)
        if requires_ocr:
            warnings.append("Document appears to be scanned or image-only; OCR required")

        header_footer_ratio = self._header_footer_ratio(document)
        if header_footer_ratio > self.HEADER_FOOTER_THRESHOLD:
            warnings.append(
                f"Repeating header/footer detected on "
                f"{header_footer_ratio:.0%} of pages"
            )

        reading_order_confidence = self._reading_order_confidence(document, warnings)
        structure_confidence = self._structure_confidence(document, warnings)
        table_blocks = [b for b in document.blocks if b.block_type == BlockType.TABLE]
        table_confidence = self._table_confidence(document, table_blocks, warnings)

        extraction_quality = self._extraction_quality(
            document, total_chars, header_footer_ratio, requires_ocr
        )

        overall = median(
            [
                extraction_quality,
                reading_order_confidence,
                structure_confidence,
                table_confidence,
            ]
        )
        requires_manual_review = overall < self.MANUAL_REVIEW_THRESHOLD
        if requires_manual_review:
            warnings.append("Overall extraction confidence below review threshold")

        return DocumentQualityReport(
            extraction_quality=round(extraction_quality, 3),
            reading_order_confidence=round(reading_order_confidence, 3),
            structure_confidence=round(structure_confidence, 3),
            table_detection_confidence=round(table_confidence, 3),
            warnings=warnings,
            requires_ocr=requires_ocr,
            requires_manual_review=requires_manual_review,
        )

    # ------------------------------------------------------------------
    # Individual detectors
    # ------------------------------------------------------------------

    def _is_scanned(self, document: CanonicalDocument, total_chars: int) -> bool:
        if document.total_pages == 0:
            return True
        return total_chars / document.total_pages < self.MIN_CHARS_PER_PAGE

    def _header_footer_ratio(self, document: CanonicalDocument) -> float:
        """Share of pages whose first or last line repeats across pages."""
        if document.total_pages == 0:
            return 0.0

        first_lines: list[str] = []
        last_lines: list[str] = []
        current_page: int | None = None
        page_first: list[str] = []
        page_last: list[str] = []

        for block in document.blocks:
            page = block.page_start or 0
            if current_page is None:
                current_page = page
            if page != current_page:
                if page_first:
                    first_lines.append(page_first[0])
                    last_lines.append(page_last[-1])
                page_first, page_last = [], []
                current_page = page
            if block.text.strip():
                if not page_first:
                    page_first.append(block.text.strip())
                page_last.append(block.text.strip())
        if page_first:
            first_lines.append(page_first[0])
            last_lines.append(page_last[-1])

        if len(first_lines) < 3:
            # Header/footer repetition needs several pages to be meaningful;
            # a single-page document always "repeats" its own first line.
            return 0.0

        from collections import Counter

        repeated_first = Counter(first_lines).most_common(1)[0][1]
        repeated_last = Counter(last_lines).most_common(1)[0][1]
        return max(repeated_first, repeated_last) / len(first_lines)

    def _reading_order_confidence(
        self, document: CanonicalDocument, warnings: list[str]
    ) -> float:
        confidence = 1.0

        # Blocks with decreasing page numbers indicate crossed reading order
        pages = [b.page_start for b in document.blocks if b.page_start is not None]
        inversions = sum(1 for a, b in zip(pages, pages[1:]) if b < a)
        if pages and inversions / len(pages) > 0.1:
            confidence -= 0.35
            warnings.append("Possible crossed reading order across pages")

        # Two-column heuristic: many alternating x0 positions per page
        if self._looks_two_column(document):
            confidence -= 0.15
            warnings.append("Two-column layout detected; verify reading order")

        # Replacement characters signal broken text extraction
        total_chars = sum(len(b.text) for b in document.blocks)
        if total_chars:
            replacement_ratio = (
                sum(b.text.count("�") for b in document.blocks) / total_chars
            )
            if replacement_ratio > 0.01:
                confidence -= 0.3
                warnings.append(
                    f"Lost characters detected ({replacement_ratio:.1%} of text)"
                )

        return max(confidence, 0.0)

    @staticmethod
    def _looks_two_column(document: CanonicalDocument) -> bool:
        positions = [
            b.bbox.x0 for b in document.blocks if b.bbox is not None and b.text.strip()
        ]
        if len(positions) < 40:
            return False
        from collections import Counter

        buckets = Counter(round(p / 50) * 50 for p in positions)
        distinct_modes = sum(1 for count in buckets.values() if count > len(positions) * 0.2)
        return distinct_modes >= 2

    def _structure_confidence(
        self, document: CanonicalDocument, warnings: list[str]
    ) -> float:
        if not document.blocks:
            warnings.append("No content blocks extracted")
            return 0.0

        confidence = 1.0
        headings = [b for b in document.blocks if b.block_type == BlockType.HEADING]
        paragraphs = [
            b for b in document.blocks if b.block_type == BlockType.PARAGRAPH
        ]

        if not headings and len(paragraphs) > 20:
            confidence -= 0.2
            warnings.append("No headings detected in a long document")

        # Orphan headings (heading followed immediately by another heading)
        orphans = 0
        for index, block in enumerate(document.blocks[:-1]):
            if block.block_type == BlockType.HEADING:
                nxt = document.blocks[index + 1]
                if nxt.block_type == BlockType.HEADING:
                    orphans += 1
        if headings and orphans / len(headings) > 0.5:
            confidence -= 0.25
            warnings.append("Many orphan headings; structure may be unreliable")

        # Very short paragraphs often indicate extraction fragmentation
        if paragraphs:
            avg_len = median(len(p.text) for p in paragraphs)
            if avg_len < 40:
                confidence -= 0.2
                warnings.append("Paragraphs unusually short; possible fragmentation")

        return max(confidence, 0.0)

    def _table_confidence(
        self,
        document: CanonicalDocument,
        table_blocks: list[DocumentBlock],
        warnings: list[str],
    ) -> float:
        if not table_blocks:
            return 1.0  # no tables to assess

        confidence = 1.0
        ragged = 0
        for table in table_blocks:
            if not table.table_data:
                ragged += 1
                continue
            widths = {len(row) for row in table.table_data}
            if len(widths) > 1:
                ragged += 1
        ratio = ragged / len(table_blocks)
        if ratio > 0:
            confidence -= 0.5 * ratio
            warnings.append(f"{ragged}/{len(table_blocks)} tables have broken structure")
        return max(confidence, 0.0)

    def _extraction_quality(
        self,
        document: CanonicalDocument,
        total_chars: int,
        header_footer_ratio: float,
        requires_ocr: bool,
    ) -> float:
        if requires_ocr:
            return 0.0

        confidence = 1.0
        if document.total_pages:
            chars_per_page = total_chars / document.total_pages
            if chars_per_page < 200:
                confidence -= 0.3

        confidence -= 0.3 * header_footer_ratio
        return max(confidence, 0.0)
