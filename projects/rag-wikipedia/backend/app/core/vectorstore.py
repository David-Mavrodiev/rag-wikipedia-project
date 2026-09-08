from __future__ import annotations

import logging
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

logger = logging.getLogger(__name__)

class QdrantStore:
    def __init__(self, url: str, collection: str):
        # generous timeout: bulk upserts under I/O load exceed the client's 5s default
        self._client = QdrantClient(url=url, timeout=60)
        self._collection = collection

    def ensure_collection(self, dim: int) -> None:
        """Create the collection at *dim*, or verify the existing one agrees.

        *dim* has no default on purpose. It used to default to a module-level
        EXPECTED_DIM of 384 and raise for anything else, which pinned the whole
        store to bge-small: `ensure_collection(dim=768)` - a Vertex
        text-embedding-005 or bge-base vector - was rejected as INVALID rather
        than compared as a mismatch. Callers pass `embedder.dim`, so the number
        comes from the model that will actually produce the vectors.

        The check this replaces is strictly WIDER, not looser. The old one
        compared *dim* against a constant and then, if the collection already
        existed, did nothing at all - so the failure that actually happens,
        re-ingesting a 768-d embedder into a collection created at 384, was
        never caught here. It surfaced later as a rejected upsert, far from its
        cause. Reading the stored size turns that into a startup failure that
        names both numbers.

        Vectors of different dimensions are not interchangeable, so a mismatch
        is never something to repair in place: build a NEW collection and
        re-ingest, exactly as a change of profile would.
        """
        existing = [collection.name for collection in self._client.get_collections().collections]
        if self._collection not in existing:
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            )
            logger.info("Created collection '%s' dim=%s", self._collection, dim)
            return

        stored = self._stored_vector_size()
        if stored != dim:
            raise ValueError(
                f"Collection '{self._collection}' stores {stored}-d vectors but the "
                f"embedder produces {dim}-d. Vectors of different dimensions are not "
                f"interchangeable - re-ingest into a NEW collection instead."
            )

    def _stored_vector_size(self) -> int:
        """The configured vector length of the existing collection."""
        vectors = self._client.get_collection(self._collection).config.params.vectors
        if not isinstance(vectors, VectorParams):
            # This store only ever creates unnamed single-vector collections. A
            # named-vector config belongs to something this store did not make;
            # refuse rather than pick one of its sizes and hope.
            raise ValueError(
                f"Collection '{self._collection}' uses a named-vector configuration, "
                f"which this store does not create or support."
            )
        return int(vectors.size)

    def upsert_batch(self, points: list[dict[str, Any]]) -> None:
        structs = [
            PointStruct(id=point["id"], vector=point["vector"], payload=point["payload"])
            for point in points
        ]
        self._client.upsert(collection_name=self._collection, points=structs)

    def search(self, vector: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        # query_points replaced the removed search() in qdrant-client >= 1.12
        results = self._client.query_points(
            collection_name=self._collection,
            query=vector,
            limit=top_k,
            with_payload=True,
        ).points
        return [
            {
                "score": result.score,
                "text": result.payload.get("text", ""),
                "title": result.payload.get("title", ""),
                "source_id": result.payload.get("source_id", ""),
            }
            for result in results
        ]

    def existing_ids(self, point_ids: list[str]) -> set[str]:
        """Return the subset of *point_ids* already stored.

        Used to make ingestion resumable: embedding is ~97% of ingestion time,
        so skipping work that is already done is what turns a 15-hour run into
        one that can be interrupted and continued. The upsert was always
        idempotent thanks to deterministic IDs - the expensive part was not.

        Qdrant stores our 32-char hex IDs as dashed UUIDs and echoes them back
        in that form, so the response is normalized before comparison. Lookup by
        the bare hex form works; the two spellings denote the same point.
        """
        if not point_ids:
            return set()

        found = self._client.retrieve(
            collection_name=self._collection,
            ids=list(point_ids),
            with_payload=False,
            with_vectors=False,
        )
        stored = {str(record.id).replace("-", "") for record in found}
        return {pid for pid in point_ids if pid.replace("-", "") in stored}

    def count(self) -> int:
        return self._client.count(collection_name=self._collection).count
