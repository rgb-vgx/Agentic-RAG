"""Document service — upload, enqueue, delete (Sections 4, 12)."""

import hashlib
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domain.documents.entities import DocumentStatus
from app.infrastructure.database.models import DocumentRecord
from app.infrastructure.database.repositories import DocumentRepository
from app.infrastructure.storage.s3 import ObjectStorage


class DocumentService:
    def __init__(
        self,
        session: AsyncSession,
        storage: ObjectStorage | None = None,
        redis_client=None,
    ):
        self.repository = DocumentRepository(session)
        self.storage = storage or ObjectStorage()
        self.redis = redis_client

    async def upload(
        self,
        *,
        tenant_id: str,
        user_id: UUID,
        knowledge_base_id: str,
        filename: str,
        content_type: str,
        data: bytes,
    ) -> DocumentRecord:
        document_id = uuid4()

        # Defense in depth: validate even when called outside the API layer
        from app.ingestion.upload_validation import validate_upload

        validate_upload(data, filename, content_type)

        file_hash = hashlib.sha256(data).hexdigest()
        file_type = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"

        key = ObjectStorage.key_for(tenant_id, str(document_id), filename)
        self.storage.upload(key, data, content_type)

        record = DocumentRecord(
            id=document_id,
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            user_id=user_id,
            filename=filename,
            storage_path=key,
            file_hash=file_hash,
            file_type=file_type,
            status=DocumentStatus.UPLOADED.value,
        )
        await self.repository.add(record)

        # Enqueue background ingestion (RQ) — retried, idempotent by document id
        self._enqueue(document_id, tenant_id, key)
        return record

    def _enqueue(self, document_id: UUID, tenant_id: str, storage_path: str) -> None:
        if self.redis is None:
            return  # wiring deferred — job submission added with worker pass
        from rq import Queue

        queue = Queue(settings.ingestion_queue, connection=self.redis)
        queue.enqueue(
            "app.workers.ingestion_job.process_document",
            str(document_id),
            tenant_id,
            storage_path,
            job_id=f"ingest-{document_id}",  # idempotent: one job per document
            job_timeout=600,
            retry=3,
        )

    async def request_delete(self, document_id: UUID, tenant_id: str) -> None:
        """Soft-delete: status → DELETING; the worker removes vectors + file."""
        record = await self.repository.get(document_id, tenant_id)
        if record is None:
            return
        record.status = DocumentStatus.DELETING.value
        await self.repository.update_status(document_id, DocumentStatus.DELETING.value)
