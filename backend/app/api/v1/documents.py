"""Document upload / listing / deletion endpoints (Section 12).

Visibility: `org` documents are shared inside the tenant, `personal` ones
are visible to their owner only — even for admins. Delete is gated by
can_manage (personal -> owner only, org -> owner or admin) and is a soft
delete: status -> DELETING, a background job retires the chunk set.
"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal, get_principal, require_role
from app.domain.documents.entities import DocumentStatus
from app.infrastructure.database.models import DocumentRecord
from app.infrastructure.database.repositories import DocumentRepository
from app.infrastructure.database.session import get_session

router = APIRouter()


def not_found() -> HTTPException:
    # Same message for "does not exist" and "not yours to see".
    return HTTPException(status.HTTP_404_NOT_FOUND, "Not found")


class DocumentResponse(BaseModel):
    id: UUID
    filename: str
    status: DocumentStatus
    file_type: str
    scope: str
    owned_by_me: bool
    quarantined: bool
    active: bool
    error_message: str | None = None
    quality_summary: dict | None = None

    @classmethod
    def from_record(cls, record: DocumentRecord, principal: Principal) -> "DocumentResponse":
        quality = None
        if record.quality_report is not None:
            quality = {
                "extraction_quality": record.quality_report.get("extraction_quality"),
                "requires_ocr": record.quality_report.get("requires_ocr"),
                "requires_manual_review": record.quality_report.get("requires_manual_review"),
                "warnings": record.quality_report.get("warnings", []),
            }
        return cls(
            id=record.id,
            filename=record.filename,
            status=DocumentStatus(record.status),
            file_type=record.file_type,
            scope=record.scope,
            owned_by_me=record.user_id == principal.user_id,
            quarantined=record.quarantined_at is not None,
            active=record.active_chunk_set_id is not None,
            error_message=record.error_message,
            quality_summary=quality,
        )


class DocumentListResponse(BaseModel):
    items: list[DocumentResponse] = Field(default_factory=list)
    total: int = 0


def _repo(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> DocumentRepository:
    return DocumentRepository(session, principal)


@router.post("", response_model=DocumentResponse, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    scope: Literal["org", "personal"] = Form(...),   # required, no default
    repo: DocumentRepository = Depends(_repo),
) -> DocumentResponse:
    """Upload a document and enqueue it for asynchronous ingestion.

    Validates size/format at the boundary (never parses in-request), then
    the service layer persists to object storage, creates the document row
    and enqueues an RQ ingestion job. Object storage / Redis wiring is
    pending, so the endpoint currently stops right after validation.
    """
    from app.ingestion.upload_validation import UploadValidationError, validate_upload

    data = await file.read()
    try:
        validate_upload(data, file.filename or "", file.content_type)
    except UploadValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    raise HTTPException(status_code=501, detail="Ingestion service not wired yet")


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    knowledge_base_id: str | None = None,
    repo: DocumentRepository = Depends(_repo),
) -> DocumentListResponse:
    principal: Principal = repo.principal
    records = await repo.list(knowledge_base_id=knowledge_base_id)
    return DocumentListResponse(
        items=[DocumentResponse.from_record(r, principal) for r in records],
        total=len(records),
    )


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: UUID, repo: DocumentRepository = Depends(_repo)
) -> DocumentResponse:
    record = await repo.get(document_id)
    if record is None:
        raise not_found()
    return DocumentResponse.from_record(record, repo.principal)


@router.get("/{document_id}/chunks")
async def get_document_chunks(
    document_id: UUID, repo: DocumentRepository = Depends(_repo)
) -> dict:
    # every sub-resource of a document goes through the same visibility gate
    if await repo.get(document_id) is None:
        raise not_found()
    return {"chunks": []}


@router.delete("/{document_id}", status_code=204)
async def delete_document(
    document_id: UUID, repo: DocumentRepository = Depends(_repo)
) -> None:
    """Soft-delete: status → DELETING, background job removes vectors + files."""
    result = await repo.delete(document_id)
    if result == "not_found":
        raise not_found()
    if result == "forbidden":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")


@router.post("/{document_id}/quarantine")
async def quarantine_document(
    document_id: UUID,
    principal: Principal = Depends(require_role("admin")),
    repo: DocumentRepository = Depends(_repo),
) -> DocumentResponse:
    record = await repo.quarantine(document_id)
    if record is None:
        raise not_found()
    return DocumentResponse.from_record(record, principal)
