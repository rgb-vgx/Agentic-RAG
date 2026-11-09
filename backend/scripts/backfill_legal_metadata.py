"""Backfill document identity and article metadata without re-embedding.

Run inside the API container so it uses the same database, object storage
and Qdrant endpoints:

    docker compose exec api python scripts/backfill_legal_metadata.py
    docker compose exec api python scripts/backfill_legal_metadata.py --force

Idempotent: safe to run again after fixes. --force re-parses every source
document (use after changing the extractor).
"""

import asyncio
import logging
import sys
from pathlib import Path

from sqlalchemy import select

# Direct execution puts scripts/ (not the project root) on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("backfill-legal-metadata")

BATCH_SIZE = 128


def _article_payload(heading_path: list[str] | None) -> dict:
    from app.ingestion.legal_metadata import extract_article

    number, title = extract_article(heading_path)
    if not number:
        return {}
    return {
        "article_number": number,
        "article_title": title,
        "legal_unit_id": None,  # filled per document below
    }


async def _backfill_documents(session) -> list[dict]:
    from app.infrastructure.database.models import DocumentRecord
    from app.infrastructure.storage.s3 import ObjectStorage
    from app.ingestion.legal_metadata import (
        extract_document_metadata,
        normalize_text,
    )
    from app.ingestion.parsers import get_parser

    storage = ObjectStorage()
    records = list((await session.execute(select(DocumentRecord))).scalars())
    result: list[dict] = []
    force = "--force" in sys.argv
    for record in records:
        title = record.title
        number = record.document_number
        year = record.document_year
        if force or not title or not number:
            try:
                data = storage.download(record.storage_path)
                ir = get_parser(record.file_type).parse(
                    data,
                    document_id=str(record.id),
                    filename=record.filename,
                )
                metadata = extract_document_metadata(record.filename, ir)
                record.title = title = metadata.title or title
                record.title_normalized = (
                    metadata.title_normalized
                    or (normalize_text(title) if title else None)
                )
                record.document_number = number = (
                    metadata.document_number or number
                )
                record.document_year = year = metadata.document_year or year
                log.info(
                    "metadata %s -> title=%r number=%r year=%r",
                    record.filename,
                    record.title,
                    record.document_number,
                    record.document_year,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("cannot parse %s: %s", record.filename, exc)
        result.append(
            {
                "id": str(record.id),
                "filename": record.filename,
                "title": title,
                "title_normalized": record.title_normalized
                or (normalize_text(title) if title else None),
                "document_number": number,
                "document_year": year,
            }
        )
    await session.commit()
    return result


def _backfill_qdrant(documents: list[dict]) -> None:
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    from app.infrastructure.indexing_factory import build_indexer

    indexer = build_indexer()
    indexer.ensure_collection()
    indexer._ensure_payload_indexes()  # noqa: SLF001 - explicit backfill hook
    client = indexer.client
    collection = indexer.collection

    for doc in documents:
        offset = None
        total = 0
        with_article = 0
        while True:
            records, offset = client.scroll(
                collection_name=collection,
                scroll_filter=Filter(
                    must=[
                        FieldCondition(
                            key="document_id",
                            match=MatchValue(value=doc["id"]),
                        )
                    ]
                ),
                limit=1000,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            if not records:
                break
            base_payload = {
                "document_title": doc["title"],
                "document_number": doc["document_number"],
                "document_year": doc["document_year"],
                "metadata_version": 1,
            }
            groups: dict[tuple, list] = {}
            for record in records:
                payload = record.payload or {}
                article = _article_payload(payload.get("heading_path"))
                if article:
                    article["legal_unit_id"] = (
                        f"{doc['id']}:article:{article['article_number']}"
                    )
                    with_article += 1
                body = {**base_payload, **article}
                key = tuple(sorted(body.items()))
                groups.setdefault(key, []).append(record.id)

            for payload_key, point_ids in groups.items():
                payload = dict(payload_key)
                for start in range(0, len(point_ids), BATCH_SIZE):
                    client.set_payload(
                        collection_name=collection,
                        payload=payload,
                        points=point_ids[start : start + BATCH_SIZE],
                        wait=True,
                    )
            total += len(records)
            if offset is None:
                break

        log.info(
            "qdrant %s: %d points (%d with article)",
            doc["filename"],
            total,
            with_article,
        )


async def main() -> None:
    from app.infrastructure.database.session import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        documents = await _backfill_documents(session)
    log.info("documents backfilled: %d", len(documents))
    await asyncio.to_thread(_backfill_qdrant, documents)
    log.info("done")


if __name__ == "__main__":
    asyncio.run(main())
