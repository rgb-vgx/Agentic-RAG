"""Canonical Document IR (Section 4 of the proposal).

The IR is the single intermediate representation every downstream stage
(analyzer, section builder, chunker) consumes. Parsers map their native
output onto these types and must carry provenance (page, bounding box,
block index) for every block.
"""

from enum import StrEnum

from pydantic import BaseModel, Field


class BlockType(StrEnum):
    PARAGRAPH = "paragraph"
    HEADING = "heading"
    LIST_ITEM = "list_item"
    TABLE = "table"
    CODE = "code"
    IMAGE = "image"
    PAGE_BREAK = "page_break"


class BoundingBox(BaseModel):
    page: int = 0
    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0


class DocumentBlock(BaseModel):
    """Atomic piece of extracted content with provenance."""

    block_id: str
    block_type: BlockType
    text: str = ""
    bbox: BoundingBox | None = None

    # Provenance back to the original file
    page_start: int | None = None
    page_end: int | None = None
    block_index: int = 0

    # Heading context inherited from preceding headings
    heading_path: list[str] = Field(default_factory=list)

    # For tables: serialized markdown (or HTML) representation
    table_data: list[list[str]] | None = None

    # Free-form parser metadata (font size, is_bold, ...)
    metadata: dict = Field(default_factory=dict)


class DocumentSection(BaseModel):
    """Hierarchical section produced by the Section Builder."""

    section_id: str
    title: str
    level: int = 1
    heading_path: list[str] = Field(default_factory=list)

    blocks: list[DocumentBlock] = Field(default_factory=list)
    subsections: list["DocumentSection"] = Field(default_factory=list)

    page_start: int | None = None
    page_end: int | None = None

    @property
    def text(self) -> str:
        return "\n\n".join(block.text for block in self.blocks if block.text)


class CanonicalDocument(BaseModel):
    """Top-level IR for one parsed document."""

    document_id: str
    filename: str
    file_type: str

    blocks: list[DocumentBlock] = Field(default_factory=list)
    sections: list[DocumentSection] = Field(default_factory=list)

    total_pages: int = 0
    language: str | None = None

    parser_name: str = ""
    parser_version: str = ""

    # Parser-specific document identity/type (e.g. scientific title, arxiv_id).
    metadata: dict = Field(default_factory=dict)
