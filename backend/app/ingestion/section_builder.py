"""Section Builder (Section 4.2): flat blocks → hierarchical sections."""

from app.ingestion.ir import BlockType, CanonicalDocument, DocumentBlock, DocumentSection


class SectionBuilder:
    """Groups blocks into a section tree using detected headings.

    Heading levels come from parser metadata (font size / markdown markers).
    When the parser did not mark headings, font-size ranking in the analyzer
    output is used as a fallback.
    """

    MAX_SECTION_LEVEL = 6

    def build(self, document: CanonicalDocument) -> CanonicalDocument:
        heading_levels = self._assign_heading_levels(document.blocks)
        sections = self._group(document.blocks, heading_levels)
        document.sections = sections
        self._propagate_heading_paths(document.sections, [])
        return document

    # ------------------------------------------------------------------

    def _assign_heading_levels(self, blocks: list[DocumentBlock]) -> dict[str, int]:
        """Map block_id → heading level (1..6) for heading blocks."""
        levels: dict[str, int] = {}
        heading_blocks = [b for b in blocks if b.block_type == BlockType.HEADING]
        if not heading_blocks:
            return levels

        # Prefer explicit parser metadata when present
        explicit = [
            b for b in heading_blocks if "heading_level" in b.metadata
        ]
        if len(explicit) == len(heading_blocks):
            return {
                b.block_id: min(int(b.metadata["heading_level"]), self.MAX_SECTION_LEVEL)
                for b in heading_blocks
            }

        # Fallback: rank by font size — largest size → level 1
        with_size = [b for b in heading_blocks if "font_size" in b.metadata]
        if with_size:
            sizes = sorted({float(b.metadata["font_size"]) for b in with_size}, reverse=True)
            for block in with_size:
                rank = sizes.index(float(block.metadata["font_size"]))
                levels[block.block_id] = min(rank + 1, self.MAX_SECTION_LEVEL)
            for block in heading_blocks:
                levels.setdefault(block.block_id, 2)
            return levels

        # Last resort: all headings are level 2 under an implicit root
        for block in heading_blocks:
            levels[block.block_id] = 2
        return levels

    def _group(
        self, blocks: list[DocumentBlock], heading_levels: dict[str, int]
    ) -> list[DocumentSection]:
        root_sections: list[DocumentSection] = []
        stack: list[DocumentSection] = []
        current: DocumentSection | None = None
        section_counter = 0

        def flush() -> None:
            nonlocal current
            if current is None:
                return
            while stack and stack[-1] is not current:
                stack.pop()
            stack.pop()
            current = None

        for block in blocks:
            level = heading_levels.get(block.block_id)
            if level is not None:
                section = DocumentSection(
                    section_id=f"s{section_counter}",
                    title=block.text,
                    level=level,
                    heading_path=[],
                    blocks=[block],
                    page_start=block.page_start,
                    page_end=block.page_end,
                )
                section_counter += 1

                while stack and stack[-1].level >= level:
                    stack.pop()
                if stack:
                    stack[-1].subsections.append(section)
                else:
                    root_sections.append(section)
                stack.append(section)
                current = section
            else:
                if current is None:
                    # Preamble content before the first heading
                    current = DocumentSection(
                        section_id=f"s{section_counter}",
                        title="Preamble",
                        level=1,
                        blocks=[],
                        page_start=block.page_start,
                        page_end=block.page_end,
                    )
                    section_counter += 1
                    root_sections.append(current)
                    stack.append(current)
                current.blocks.append(block)
                current.page_end = block.page_end or current.page_end

        return root_sections

    def _propagate_heading_paths(
        self, sections: list[DocumentSection], parent_path: list[str]
    ) -> None:
        for section in sections:
            section.heading_path = parent_path + [section.title]
            for block in section.blocks:
                if not block.heading_path:
                    block.heading_path = section.heading_path
            self._propagate_heading_paths(section.subsections, section.heading_path)
