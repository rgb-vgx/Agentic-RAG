"""Qdrant indexing: large upserts must be split into batches."""

from app.ingestion.chunking.types import Chunk
from app.ingestion.indexing import QdrantIndexer


class FakeClient:
    def __init__(self) -> None:
        self.batches: list[list] = []

    def upsert(self, collection_name, points, wait=True):  # noqa: ANN001
        self.batches.append(points)


def _item(index: int) -> dict:
    chunk = Chunk(chunk_id=f"c{index}", document_id="doc-1", content="x")
    return {
        "chunk": chunk,
        "vector": [0.1] * 4,
        "payload": {"chunk_id": chunk.chunk_id, "content": chunk.content},
    }


def test_upsert_chunks_is_batched():
    client = FakeClient()
    indexer = QdrantIndexer(
        client,  # type: ignore[arg-type]
        collection="chunks",
        dense_dim=4,
        enable_sparse=False,
        upsert_batch_size=2,
    )

    written = indexer.upsert_chunks(
        [_item(i) for i in range(5)], chunk_set_id="cs-1"
    )

    assert written == 5
    assert [len(batch) for batch in client.batches] == [2, 2, 1]
