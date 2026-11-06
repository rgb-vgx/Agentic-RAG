"""Chunk domain type shared by the ingestion pipeline."""

from pydantic import BaseModel, Field


class Chunk(BaseModel):
    """A retrievable unit of content produced by adaptive chunking."""

    chunk_id: str
    document_id: str
    section_id: str = ""

    content: str
    token_count: int = 0

    page_start: int | None = None
    page_end: int | None = None
    heading_path: list[str] = Field(default_factory=list)
    block_types: list[str] = Field(default_factory=list)

    # Strategy that produced this chunk (for versioning snapshot, Section 9.7)
    strategy_name: str = ""
    strategy_version: str = "1.0"

    # Positional signals used by retrieval (parent-doc / small-to-big)
    chunk_index: int = 0
    parent_chunk_id: str | None = None

    metadata: dict = Field(default_factory=dict)
