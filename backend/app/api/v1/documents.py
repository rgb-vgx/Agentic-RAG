"""Document upload / listing / deletion endpoints (Section 12)."""

from uuid import UUID

from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel, Field

from app.domain.documents.entities import Document, DocumentStatus

router = APIRouter()


class DocumentResponse(BaseModel):
    id: UUID
    filename: str
    status: DocumentStatus
    file_type: str
    error_message: str | None = None
    quality_summary: dict | None = None

    @classmethod
    def from_document(cls, document: Document) -> "DocumentResponse":
        quality = None
        if document.quality_report is not None:
            quality = {
                "extraction_quality": document.quality_report.extraction_quality,
                "requires_ocr": document.quality_report.requires_ocr,
                "requires_manual_review": document.quality_report.requires_manual_review,
                "warnings": document.quality_report.warnings,
            }
        return cls(
            id=document.id,
            filename=document.filename,
            status=document.status,
            file_type=document.file_type,
            error_message=document.error_message,
            quality_summary=quality,
        )


class DocumentListResponse(BaseModel):
    items: list[DocumentResponse] = Field(default_factory=list)
    total: int = 0


@router.post("", response_model=DocumentResponse, status_code=201)
async def upload_document(file: UploadFile = File(...)) -> DocumentResponse:
    """Upload a document and enqueue it for asynchronous ingestion.

    The service layer persists the file to object storage, creates the
    document row and enqueues an RQ ingestion job. Wiring is added in the
    services pass; this endpoint defines the contract.
    """
    raise HTTPException(status_code=501, detail="Ingestion service not wired yet")


@router.get("", response_model=DocumentListResponse)
async def list_documents(knowledge_base_id: UUID | None = None) -> DocumentListResponse:
    raise HTTPException(status_code=501, detail="Document repository not wired yet")


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(document_id: UUID) -> DocumentResponse:
    raise HTTPException(status_code=501, detail="Document repository not wired yet")


@router.delete("/{document_id}", status_code=204)
async def delete_document(document_id: UUID) -> None:
    """Soft-delete: status → DELETING, background job removes vectors + files."""
    raise HTTPException(status_code=501, detail="Document repository not wired yet")
