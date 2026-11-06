"""Document domain entities and lifecycle enums."""

from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class DocumentStatus(StrEnum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    READY = "ready"
    DEGRADED = "degraded"  # quality assessment flagged issues but processing continued
    FAILED = "failed"
    DELETING = "deleting"
    DELETED = "deleted"


class DocumentQualityReport(BaseModel):
    """Output of Document Quality Assessment (Section 4.4 of the proposal)."""

    extraction_quality: float = Field(ge=0.0, le=1.0, default=1.0)
    reading_order_confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    structure_confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    table_detection_confidence: float = Field(ge=0.0, le=1.0, default=1.0)

    warnings: list[str] = Field(default_factory=list)

    requires_ocr: bool = False
    requires_manual_review: bool = False

    @property
    def is_acceptable(self) -> bool:
        return not self.requires_ocr


class DocumentRelationship(BaseModel):
    """Basic document relationship metadata (Section 9.6)."""

    related_document_ids: list[UUID] = Field(default_factory=list)
    references: list[UUID] = Field(default_factory=list)
    supersedes: list[UUID] = Field(default_factory=list)
    document_version: str | None = None
    effective_from: str | None = None
    effective_until: str | None = None


class Document(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    tenant_id: str
    knowledge_base_id: str
    user_id: UUID

    filename: str
    storage_path: str
    file_hash: str
    file_type: str

    status: DocumentStatus = DocumentStatus.UPLOADED
    error_message: str | None = None
    quality_report: DocumentQualityReport | None = None
    relationship: DocumentRelationship = Field(default_factory=DocumentRelationship)

    # Ingestion run snapshot (chunk_set_id, strategy_runs, timing) — Section 9.7
    run_metadata: dict = Field(default_factory=dict)

    created_at: str | None = None
    updated_at: str | None = None
