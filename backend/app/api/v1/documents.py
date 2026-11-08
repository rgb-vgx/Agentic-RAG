"""Document upload / listing / deletion endpoints (Section 12).

Visibility: `org` documents are shared inside the tenant, `personal` ones
are visible to their owner only — even for admins. Delete is gated by
can_manage (personal -> owner only, org -> owner or admin) and is a soft
delete: status -> DELETING, a background job retires the chunk set.
"""

import asyncio
import logging
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from redis import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal, get_principal, require_role
from app.config import settings
from app.domain.documents.entities import DocumentStatus
from app.infrastructure.database.models import DocumentRecord
from app.infrastructure.database.repositories import DocumentRepository
from app.infrastructure.database.session import get_session
from app.infrastructure.storage.s3 import ObjectStorage
from app.services.document_service import DocumentService

router = APIRouter()
logger = logging.getLogger(__name__)


def not_found() -> HTTPException:
    # Same message for "does not exist" and "not yours to see".
    return HTTPException(status.HTTP_404_NOT_FOUND, "Not found")


_CONTENT_TYPES = {
    "pdf": "application/pdf",
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    "txt": "text/plain; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "markdown": "text/markdown; charset=utf-8",
}


def content_disposition(filename: str) -> str:
    """RFC 6266 attachment header that survives non-ASCII filenames."""
    ascii_name = filename.encode("ascii", "ignore").decode().strip() or "document"
    return (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(filename)}"
    )


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


class ChunkResponse(BaseModel):
    chunk_id: str
    chunk_index: int = 0
    content: str
    heading_path: list[str] = Field(default_factory=list)
    page_start: int | None = None
    page_end: int | None = None
    strategy_name: str | None = None
    section_id: str | None = None
    parent_chunk_id: str | None = None


def _repo(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> DocumentRepository:
    return DocumentRepository(session, principal)


def _service(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> DocumentService:
    """Upload/delete use-case: storage + RQ queue, bound to the Principal."""
    return DocumentService(
        session=session,
        principal=principal,
        storage=ObjectStorage(),
        redis_client=Redis.from_url(settings.redis_url),
    )


@router.post("", response_model=DocumentResponse, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    scope: Literal["org", "personal"] = Form(...),   # required, no default
    knowledge_base_id: str = Form("default"),
    service: DocumentService = Depends(_service),
) -> DocumentResponse:
    """Upload a document and enqueue it for asynchronous ingestion.

    Validates size/format at the boundary (never parses in-request), stores
    the original, creates the document row and enqueues an RQ job. Parsing,
    chunking, embedding and indexing all happen in the worker.
    """
    from app.ingestion.upload_validation import UploadValidationError

    data = await file.read()
    try:
        record = await service.upload(
            knowledge_base_id=knowledge_base_id,
            scope=scope,
            filename=file.filename or "",
            content_type=file.content_type or "application/octet-stream",
            data=data,
        )
    except UploadValidationError as exc:
        logger.warning(
            "upload rejected: filename=%r content_type=%r reason=%s",
            file.filename,
            file.content_type,
            exc,
        )
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        # storage / queue failure — the request itself was valid
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Upload failed: {exc}",
        ) from exc
    return DocumentResponse.from_record(record, service.principal)


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


@router.get("/{document_id}/download")
async def download_document(
    document_id: UUID, repo: DocumentRepository = Depends(_repo)
) -> Response:
    """Download the original upload, gated by document visibility.

    The object is streamed through the API instead of a presigned URL:
    presigning would expose the internal MinIO hostname to the browser.
    """
    record = await repo.get(document_id)
    if record is None:
        raise not_found()
    if not record.storage_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stored file not found")

    from botocore.exceptions import ClientError

    storage = ObjectStorage()
    try:
        data = await asyncio.to_thread(storage.download, record.storage_path)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404", "NotFound"):
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Stored file not found"
            ) from exc
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Object storage unavailable"
        ) from exc

    return Response(
        content=data,
        media_type=_CONTENT_TYPES.get(
            record.file_type, "application/octet-stream"
        ),
        headers={
            "Content-Disposition": content_disposition(record.filename),
            "Cache-Control": "private, max-age=0",
        },
    )


@router.get("/{document_id}/chunks")
async def get_document_chunks(
    document_id: UUID,
    limit: int = 2000,
    repo: DocumentRepository = Depends(_repo),
    principal: Principal = Depends(get_principal),
) -> dict:
    """Chunks of the document's active chunk set, read from Qdrant.

    Visibility is enforced twice: the document must be visible to the
    Principal, and the Qdrant filter carries tenant + document + chunk-set
    + active, so a stale or foreign point can never leak through.
    """
    record = await repo.get(document_id)
    if record is None:
        raise not_found()

    chunk_set_id = record.active_chunk_set_id
    if not chunk_set_id:
        return {"items": [], "total": 0, "chunk_set_id": None}

    from qdrant_client.http.exceptions import UnexpectedResponse
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    from app.infrastructure.indexing_factory import get_async_qdrant_client

    client = get_async_qdrant_client()
    scroll_filter = Filter(
        must=[
            FieldCondition(
                key="tenant_id", match=MatchValue(value=str(principal.tenant_id))
            ),
            FieldCondition(
                key="document_id", match=MatchValue(value=str(document_id))
            ),
            FieldCondition(
                key="chunk_set_id", match=MatchValue(value=chunk_set_id)
            ),
            FieldCondition(key="active", match=MatchValue(value=True)),
        ]
    )
    max_items = max(1, min(limit, 5000))
    points = []
    next_offset = None
    try:
        # Scroll in batches: a 182-page law document has ~845 chunks, far
        # more than a single scroll page. `total` must reflect all of them.
        while len(points) < max_items:
            batch, next_offset = await client.scroll(
                collection_name=settings.qdrant_collection,
                scroll_filter=scroll_filter,
                limit=min(512, max_items - len(points)),
                offset=next_offset,
                with_payload=True,
                with_vectors=False,
            )
            points.extend(batch)
            if next_offset is None:
                break
    except UnexpectedResponse as exc:
        if getattr(exc, "status_code", None) == 404:
            # Collection not created yet (nothing ingested on this backend)
            return {"items": [], "total": 0, "chunk_set_id": chunk_set_id}
        raise

    items = []
    for point in points:
        payload = point.payload or {}
        if payload.get("tenant_id") != str(principal.tenant_id):
            continue
        items.append(
            ChunkResponse(
                chunk_id=str(payload.get("chunk_id", point.id)),
                chunk_index=int(payload.get("chunk_index") or 0),
                content=payload.get("content", ""),
                heading_path=list(payload.get("heading_path") or []),
                page_start=payload.get("page_start"),
                page_end=payload.get("page_end"),
                strategy_name=payload.get("strategy_name"),
                section_id=payload.get("section_id"),
                parent_chunk_id=payload.get("parent_chunk_id"),
            )
        )
    # `section_id` is assigned sequentially by the Section Builder (s0, s1,
    # …), which is the document's real reading order — unlike heading text,
    # which sorts alphabetically (Chương IX before Chương V).
    def section_order(chunk: ChunkResponse) -> int:
        section_id = chunk.section_id or ""
        if section_id.startswith("s") and section_id[1:].isdigit():
            return int(section_id[1:])
        return 10**9

    items.sort(
        key=lambda chunk: (
            section_order(chunk),
            chunk.chunk_index,
            chunk.chunk_id,
        )
    )
    return {
        "items": [item.model_dump() for item in items],
        "total": len(items),
        "truncated": next_offset is not None,
        "chunk_set_id": chunk_set_id,
    }


@router.delete("/{document_id}", status_code=204)
async def delete_document(
    document_id: UUID, service: DocumentService = Depends(_service)
) -> None:
    """Soft-delete: status → DELETING, background job removes vectors + files."""
    result = await service.request_delete(document_id)
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
