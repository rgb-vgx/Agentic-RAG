"""RQ ingestion worker (Section 12, 16.2).

    process_document(document_id, tenant_id, storage_path)

Idempotency: one job per document (job_id = ingest-{id}); a document that
already reached READY is skipped. Failures set status=FAILED and are
retried by RQ with exponential backoff.
"""

import logging

logger = logging.getLogger(__name__)


def process_document(document_id: str, tenant_id: str, storage_path: str) -> dict:
    """RQ entrypoint — runs synchronously in the worker process.

    The pipeline itself is async (SQLAlchemy async + async embedding calls),
    so the worker bridges with asyncio.run.
    """
    import asyncio

    return asyncio.run(
        _process_async(document_id, tenant_id, storage_path)
    )


async def _process_async(document_id: str, tenant_id: str, storage_path: str) -> dict:
    from sqlalchemy import select

    from app.domain.documents.entities import Document, DocumentStatus
    from app.infrastructure.database.models import DocumentRecord
    from app.infrastructure.database.session import get_session_factory
    from app.infrastructure.embeddings import get_embedding_provider
    from app.infrastructure.indexing_factory import build_indexer
    from app.infrastructure.storage.s3 import ObjectStorage
    from app.ingestion.embedding import EmbeddingPipeline
    from app.ingestion.pipeline import IngestionPipeline

    factory = get_session_factory()
    async with factory() as session:
        record = await session.get(DocumentRecord, document_id)
        if record is None:
            return {"status": "skipped", "reason": "document not found"}
        if record.tenant_id != tenant_id:
            return {"status": "skipped", "reason": "tenant mismatch"}
        if record.status == DocumentStatus.READY.value:
            return {"status": "skipped", "reason": "already ready"}

        storage = ObjectStorage()
        try:
            data = storage.download(record.storage_path)
        except Exception as exc:
            await _mark_failed(session, record, f"storage download failed: {exc}")
            return {"status": "failed", "error": str(exc)}

        document = Document(
            id=record.id,
            tenant_id=record.tenant_id,
            knowledge_base_id=record.knowledge_base_id,
            user_id=record.user_id,
            filename=record.filename,
            storage_path=record.storage_path,
            file_hash=record.file_hash,
            file_type=record.file_type,
        )

        pipeline = IngestionPipeline(
            embedding_pipeline=EmbeddingPipeline(get_embedding_provider()),
            indexer=build_indexer(),
        )
        result = await pipeline.run(document, data)

        record.status = result.status.value
        record.error_message = result.error_message
        record.quality_report = (
            result.quality_report.model_dump() if result.quality_report else None
        )
        record.run_metadata = result.run_metadata
        await session.commit()

        logger.info(
            "ingestion complete", extra={"document_id": document_id, "status": record.status}
        )
        return {"status": record.status, "chunks": len(result.run_metadata.get("strategy_runs", []))}


async def _mark_failed(session, record, message: str) -> None:
    from app.domain.documents.entities import DocumentStatus

    record.status = DocumentStatus.FAILED.value
    record.error_message = message[:2000]
    await session.commit()


def delete_document(document_id: str, tenant_id: str) -> dict:
    """RQ entrypoint: remove vectors + object + row for a deleted document."""
    import asyncio

    return asyncio.run(_delete_async(document_id, tenant_id))


async def _delete_async(document_id: str, tenant_id: str) -> dict:
    from sqlalchemy import delete

    from app.infrastructure.database.models import DocumentRecord
    from app.infrastructure.database.session import get_session_factory
    from app.infrastructure.indexing_factory import build_indexer
    from app.infrastructure.storage.s3 import ObjectStorage

    # 1. Remove vectors for this document (filtered delete — never wipe the collection)
    indexer = build_indexer()
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    indexer.client.delete(
        collection_name=indexer.collection,
        points_selector=Filter(
            must=[
                FieldCondition(key="document_id", match=MatchValue(value=document_id)),
                FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
            ]
        ),
    )

    # 2. Remove the original file
    storage = ObjectStorage()
    prefix = f"tenants/{tenant_id}/documents/{document_id}/"
    storage.delete_prefix(prefix)

    # 3. Remove the row
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            delete(DocumentRecord).where(DocumentRecord.id == document_id)
        )
        await session.commit()

    return {"status": "deleted", "document_id": document_id}
