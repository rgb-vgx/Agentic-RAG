"""Document service — upload, enqueue, delete (Sections 4, 12).

Bound to a Principal: tenant and owner always come from the token, never
from client input. Visibility/ownership rules live in DocumentRepository.
"""

import asyncio
import hashlib
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal
from app.config import settings
from app.domain.documents.entities import DocumentStatus
from app.infrastructure.database.models import DocumentRecord
from app.infrastructure.database.repositories import DocumentRepository
from app.infrastructure.storage.s3 import ObjectStorage


class DocumentService:
    def __init__(
        self,
        session: AsyncSession,
        principal: Principal,
        storage: ObjectStorage | None = None,
        redis_client=None,
    ):
        self.principal = principal
        self.repository = DocumentRepository(session, principal)
        self.storage = storage or ObjectStorage()
        self.redis = redis_client

    async def upload(
        self,
        *,
        knowledge_base_id: str,
        scope: str,
        filename: str,
        content_type: str,
        data: bytes,
        domain: str | None = None,
        domain_source: str | None = None,
        domain_confidence: float | None = None,
    ) -> DocumentRecord:
        if scope not in ("org", "personal"):
            raise ValueError("scope")
        document_id = uuid4()

        # Defense in depth: validate even when called outside the API layer
        from app.ingestion.upload_validation import validate_upload

        validate_upload(data, filename, content_type)

        tenant_id = str(self.principal.tenant_id)
        file_hash = hashlib.sha256(data).hexdigest()
        file_type = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"

        key = ObjectStorage.key_for(tenant_id, str(document_id), filename)
        # boto3 is blocking — keep the event loop free
        await asyncio.to_thread(self.storage.upload, key, data, content_type)

        record = DocumentRecord(
            id=document_id,
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            user_id=self.principal.user_id,
            scope=scope,
            filename=filename,
            storage_path=key,
            file_hash=file_hash,
            file_type=file_type,
            status=DocumentStatus.UPLOADED.value,
        )
        # Only override the model defaults when the caller supplied a domain.
        if domain is not None:
            record.domain = domain
        if domain_source is not None:
            record.domain_source = domain_source
        if domain_confidence is not None:
            record.domain_confidence = domain_confidence
        # add() re-asserts tenant/user from the Principal (defence in depth)
        await self.repository.add(record)

        # Enqueue background ingestion (RQ) — retried, idempotent by document id
        await asyncio.to_thread(self._enqueue, document_id, tenant_id, key)
        return record

    def _enqueue(self, document_id: UUID, tenant_id: str, storage_path: str) -> None:
        if self.redis is None:
            return  # wiring deferred — job submission added with worker pass
        from rq import Queue, Retry

        queue = Queue(settings.ingestion_queue, connection=self.redis)
        queue.enqueue(
            "app.workers.ingestion_job.process_document",
            str(document_id),
            tenant_id,
            storage_path,
            job_id=f"ingest-{document_id}",  # idempotent: one job per document
            job_timeout=600,
            retry=Retry(max=3, interval=[10, 30, 60]),
        )

    async def request_delete(
        self, document_id: UUID
    ) -> Literal["deleted", "not_found", "forbidden"]:
        """Soft-delete after the ownership gate; the worker removes vectors + file."""
        result = await self.repository.delete(document_id)
        if result != "deleted":
            return result
        await asyncio.to_thread(
            self._enqueue_delete, document_id, str(self.principal.tenant_id)
        )
        return result

    def _enqueue_delete(self, document_id: UUID, tenant_id: str) -> None:
        if self.redis is None:
            return
        from rq import Queue, Retry

        queue = Queue(settings.ingestion_queue, connection=self.redis)
        queue.enqueue(
            "app.workers.ingestion_job.delete_document",
            str(document_id),
            tenant_id,
            job_id=f"delete-{document_id}",
            job_timeout=300,
            retry=Retry(max=3, interval=[10, 30, 60]),
        )
